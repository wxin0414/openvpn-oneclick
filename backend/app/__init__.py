from importlib import import_module

# Expose crud module for convenience imports like `from app import crud`.
crud = import_module("app.crud")

__all__ = ["crud"]
