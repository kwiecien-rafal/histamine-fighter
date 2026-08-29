"""Server-rendered HTML pages, mirroring the JSON API's layout under ``app/api/``.

Pages take the bare paths (``/meals``) while the JSON API keeps ``/api/v1/*``, so the
two never collide and the API stays the open-source integration surface. Routes here
are thin: they call the same services through the same ``Depends`` providers as the
API, hand the resulting view models to a template, and hold no business logic. They
are kept out of the OpenAPI schema, which documents the JSON API only.

The session is resolved once for the whole router, so the shell every page extends
can show the signed-in account without any page asking for it.
"""

from fastapi import APIRouter, Depends

from app.web import admin, auth, learn, lookup, meals, pages, profile
from app.web.deps import bind_current_user

router = APIRouter(include_in_schema=False, dependencies=[Depends(bind_current_user)])
router.include_router(pages.router)
router.include_router(meals.router)
router.include_router(lookup.router)
router.include_router(learn.router)
router.include_router(auth.router)
router.include_router(profile.router)
router.include_router(admin.router)
