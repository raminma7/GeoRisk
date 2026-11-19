"""Import every domain's models so metadata is complete (Alembic, tests)."""

from app.ai.models import *  # noqa: F401,F403
from app.core.db import Base  # noqa: F401
from app.crises.models import *  # noqa: F401,F403
from app.economy.models import *  # noqa: F401,F403
from app.geopolitics.models import *  # noqa: F401,F403
from app.ingestion.models import *  # noqa: F401,F403
from app.ml.models import *  # noqa: F401,F403
from app.monitoring.models import *  # noqa: F401,F403
from app.registry.models import *  # noqa: F401,F403
