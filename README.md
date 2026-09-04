# Beldium Mining Compliance API

Backend API for the Beldium mining compliance platform. The project is a modular Django REST Framework application designed for mining companies, compliance partners, inspection bodies, laboratories, and regulators.

## Current implementation

The first domain slice includes:

- Email/password registration and JWT authentication
- Current-user profile endpoint
- Multi-organisation accounts
- Mining company, compliance partner, regulator, laboratory, and inspection-body organisation types
- Owner, administrator, reviewer, inspector, and member roles
- Private organisation membership boundaries
- Organisation invitations and invitation acceptance
- Join requests with transactional approval or rejection
- OpenAPI schema and Swagger UI

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

SQLite is used by default for local development. Set `DB_ENGINE=postgresql` and the `DB_*` variables for PostgreSQL.

## API entry points

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/auth/register/` | Register a user |
| POST | `/api/v1/auth/token/` | Obtain JWT access and refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | Refresh an access token |
| GET/PATCH | `/api/v1/auth/me/` | Read or update the current user |
| GET/POST | `/api/v1/organisations/` | List memberships or create an organisation |
| GET | `/api/v1/organisations/directory/` | Discover verified organisations |
| GET | `/api/v1/organisations/{id}/members/` | List organisation members |
| GET/POST | `/api/v1/organisations/{id}/invitations/` | Manage invitations |
| POST | `/api/v1/invitations/accept/` | Accept an invitation |
| GET/POST | `/api/v1/join-requests/` | Manage the current user's join requests |
| POST | `/api/v1/join-requests/{id}/decide/` | Approve or reject a request as an organisation administrator |
| GET | `/api/docs/` | Swagger UI |

Except for registration and token issuance, endpoints require a bearer access token.

## Validation

```bash
python manage.py check
python manage.py test
```

## Planned domain sequence

1. Mining sites, ownership, licences, and GPS boundaries
2. Compliance applications and workflow history
3. Evidence documents and information requests
4. Reviews, configurable scoring, and risk assessments
5. Inspections, findings, non-conformities, and corrective actions
6. Production, inventory, sampling, and laboratory results
7. Notifications, audit trail, dashboard aggregates, and reports
