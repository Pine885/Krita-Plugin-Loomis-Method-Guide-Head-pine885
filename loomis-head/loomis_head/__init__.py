try:
    from krita import Krita
except ImportError:
    Krita = None

if Krita is not None:
    from .loomis_head_plugin import LoomisHeadPlugin

    app = Krita.instance()
    extension = LoomisHeadPlugin(app)
    app.addExtension(extension)
