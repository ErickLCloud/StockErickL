from .indicators import compute_indicators

__all__ = ["compute_indicators", "screen", "CONDITIONS"]


def __getattr__(name):
    # Lazy: importing screener eagerly here makes `python -m src.analysis.screener`
    # emit a RuntimeWarning (module already in sys.modules before execution).
    if name in ("screen", "CONDITIONS"):
        from . import screener
        return getattr(screener, name)
    raise AttributeError(name)
