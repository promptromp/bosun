"""Empty on purpose: it makes lib/ an import root for pytest-impacted (the commit hook's test
selection; pyproject.toml, impacted_module). lib/ is a flat folder of modules on sys.path, not a
package, and the plugin only takes the folder above a regular package as a place imports start
from. Through this package it reads `import ledger` in a test, or in another lib/ module, as
lib/ledger.py, and a change to that file selects the tests that import it, directly or not."""
