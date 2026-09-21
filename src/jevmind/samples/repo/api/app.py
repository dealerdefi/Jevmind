from api.auth.routes import router as auth
from api.signals.routes import router as signals
from api.billing.routes import router as billing

def create_app():
    app = App()
    for r in (auth, signals, billing):
        app.include(r)
    return app
