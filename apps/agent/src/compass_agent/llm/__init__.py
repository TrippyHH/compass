"""LLM execution kernel.

The package has no import-time side effects and never imports the models package,
SQLAlchemy, FastAPI or the secret store. Import concrete types from their modules.
"""
