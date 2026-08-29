"""Every ORM model must be re-exported from app.models.

A model defined under app/models/ but missing from app/models/__init__.py is
never imported, so it never registers on Base.metadata: create_all (the test
schema) and Alembic autogenerate both skip its table. This walks the package and
fails when a mapped class is not on the public surface, which is the gap that let
curated_meals ship without its table on its own branch.
"""

import importlib
import inspect
import pkgutil

import app.models
from app.db.base import Base


def _defined_models() -> list[type]:
    """Mapped classes declared in each app.models submodule, not imported ones."""
    models: list[type] = []
    for module_info in pkgutil.iter_modules(app.models.__path__):
        module = importlib.import_module(f"app.models.{module_info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            declared_here = obj.__module__ == module.__name__
            if declared_here and issubclass(obj, Base) and obj is not Base:
                models.append(obj)
    return models


def test_every_model_is_exported_from_the_package() -> None:
    models = _defined_models()
    assert models, "no ORM models discovered; the package walk is broken"

    missing = sorted(
        cls.__name__ for cls in models if getattr(app.models, cls.__name__, None) is not cls
    )
    assert not missing, (
        f"models defined but not re-exported from app.models: {missing}. "
        "Add the import and __all__ entry, or create_all and autogenerate skip the table."
    )

    not_in_all = sorted(cls.__name__ for cls in models if cls.__name__ not in app.models.__all__)
    assert not not_in_all, f"models missing from app.models.__all__: {not_in_all}"
