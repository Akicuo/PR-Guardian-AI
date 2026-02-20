# Alembic Migration Versions

This directory contains database migration files.

## Creating a New Migration

```bash
# From the project root directory
alembic revision --autogenerate -m "Description of changes"
```

## Running Migrations

```bash
# Upgrade to the latest version
alembic upgrade head

# Upgrade by one step
alembic upgrade +1

# Downgrade by one step
alembic downgrade -1

# View current version
alembic current

# View migration history
alembic history
```

## Notes

- Migrations are generated automatically based on SQLAlchemy models
- Always review generated migrations before committing
- Test migrations on a local database first
- Never modify committed migrations - create new ones instead
