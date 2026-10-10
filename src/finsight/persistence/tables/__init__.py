"""SQLAlchemy table definitions. PostgreSQL is authoritative for all of them.

**Importing this package registers every table with ``Base.metadata``.** That is the
point of the imports below, and it is why they are here rather than repeated at each
call site. A table module that is not imported is invisible to ``Base.metadata``, so
Alembic autogenerate and the ``compare_metadata`` parity check both report it as a
table to *drop* — the applied schema has it and the model appears not to.

That was not hypothetical. ``answers`` and ``answer_claims`` were missing from
``migrations/env.py``'s list while a third copy of the same list lived in the parity
test, which is the only reason the gap did not show up as a failure. Three lists is
one list too many; this is the one.
"""

from finsight.persistence.tables import answers as answers
from finsight.persistence.tables import chunks as chunks
from finsight.persistence.tables import document_metadata as document_metadata
from finsight.persistence.tables import documents as documents
from finsight.persistence.tables import embedding_cache as embedding_cache
from finsight.persistence.tables import generations as generations
from finsight.persistence.tables import source as source
