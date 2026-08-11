from models.business_profile import BusinessProfile
from models.entities import Entity
from models.sources import Source
from models.session_logs import SessionLog

# It tells Python that the models folder is a package — meaning you can import from it like from models import Entity instead of having to specify the full file path every time.
# It also ensures all models are loaded in one place. When SQLAlchemy creates the tables, it needs to know all the models exist. By importing them all here, you guarantee nothing gets missed.