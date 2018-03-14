import logging

import connexion
from jinja2 import FileSystemLoader
from walkoff import helpers
from walkoff.executiondb.device import App
from walkoff.extensions import db, jwt
logger = logging.getLogger(__name__)


def register_blueprints(flaskapp):
    from walkoff.server.blueprints import custominterface
    from walkoff.server.blueprints import workflowqueue
    from walkoff.server.blueprints import notifications

    flaskapp.register_blueprint(custominterface.custom_interface_page, url_prefix='/custominterfaces/<interface>')
    flaskapp.register_blueprint(workflowqueue.workflowqueue_page, url_prefix='/api/streams/workflowqueue')
    flaskapp.register_blueprint(notifications.notifications_page, url_prefix='/api/streams/messages')
    __register_all_app_blueprints(flaskapp)


def __get_blueprints_in_module(module):
    from interfaces import AppBlueprint
    blueprints = [getattr(module, field)
                  for field in dir(module) if (not field.startswith('__')
                                               and isinstance(getattr(module, field), AppBlueprint))]
    return blueprints


def __register_app_blueprint(flaskapp, blueprint, url_prefix):
    rule = '{0}{1}'.format(url_prefix, blueprint.rule) if blueprint.rule else url_prefix
    flaskapp.register_blueprint(blueprint.blueprint, url_prefix=rule)


def __register_blueprint(flaskapp, blueprint, url_prefix):
    rule = '{0}{1}'.format(url_prefix, blueprint.rule) if blueprint.rule else url_prefix
    flaskapp.register_blueprint(blueprint.blueprint, url_prefix=rule)


def __register_app_blueprints(flaskapp, app_name, blueprints):
    url_prefix = '/interfaces/{0}'.format(app_name.split('.')[-1])
    for blueprint in blueprints:
        __register_blueprint(flaskapp, blueprint, url_prefix)


def __register_all_app_blueprints(flaskapp):
    from walkoff.helpers import import_submodules
    import interfaces
    imported_apps = import_submodules(interfaces)
    for interface_name, interfaces_module in imported_apps.items():
        try:
            display_blueprints = []
            for submodule in import_submodules(interfaces_module, recursive=True).values():
                display_blueprints.extend(__get_blueprints_in_module(submodule))
        except ImportError:
            pass
        else:
            __register_app_blueprints(flaskapp, interface_name, display_blueprints)


def create_app():
    import walkoff.config.config
    connexion_app = connexion.App(__name__, specification_dir='../api/')
    _app = connexion_app.app
    _app.jinja_loader = FileSystemLoader(['walkoff/templates'])
    _app.config.from_object('walkoff.config.config.AppConfig')

    db.init_app(_app)
    jwt.init_app(_app)
    connexion_app.add_api('composed_api.yaml')

    walkoff.config.config.initialize()
    register_blueprints(_app)

    import walkoff.server.workflowresults  # Don't delete this import
    import walkoff.messaging.utils  # Don't delete this import
    return _app


# Template Loader
app = create_app()


@app.before_first_request
def create_user():
    from walkoff import executiondb
    from walkoff.serverdb import add_user, User, Role, initialize_default_resources_admin, \
        initialize_default_resources_guest
    db.create_all()

    # Setup admin and guest roles
    initialize_default_resources_admin()
    initialize_default_resources_guest()

    # Setup admin user
    admin_role = Role.query.filter_by(id=1).first()
    admin_user = User.query.filter_by(username="admin").first()
    if not admin_user:
        add_user(username='admin', password='admin', roles=[1])
    elif admin_role not in admin_user.roles:
        admin_user.roles.append(admin_role)

    db.session.commit()

    apps = set(helpers.list_apps()) - set([_app.name
                                           for _app in executiondb.execution_db.session.query(App).all()])
    app.logger.debug('Found apps: {0}'.format(apps))
    for app_name in apps:
        executiondb.execution_db.session.add(App(name=app_name, devices=[]))
    db.session.commit()
    executiondb.execution_db.session.commit()
    send_all_cases_to_workers()
    app.logger.handlers = logging.getLogger('server').handlers


def send_all_cases_to_workers():
    from walkoff.server.flaskserver import running_context
    from walkoff.serverdb.casesubscription import CaseSubscription
    from walkoff.case.database import case_db, Case
    from walkoff.case.subscription import Subscription

    for case_subscription in CaseSubscription.query.all():
        subscriptions = [Subscription(sub['id'], sub['events']) for sub in case_subscription.subscriptions]
        case = case_db.session.query(Case).filter(Case.name == case_subscription.name).first()
        if case is not None:
            running_context.executor.update_case(case.id, subscriptions)
