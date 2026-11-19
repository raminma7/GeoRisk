# Data sources, licences and citations

| Key | Source | Access | Licence / terms | AI | ML | Export |
|---|---|---|---|---|---|---|
| `gdelt_events` | GDELT Events 2.0 (`data.gdeltproject.org/gdeltv2`) | public, 15-min files | free & open with citation | ✓ | ✓ | ✓ |
| `gdelt_doc` | GDELT DOC 2.0 API (`api.gdeltproject.org/api/v2/doc/doc`) | public, ≤1 req/5 s | metadata/links only; article text belongs to publishers | ✓ | ✓ | ✓ (metadata) |
| `ucdp_ged` | UCDP Georeferenced Event Dataset (bulk CSV) | public downloads | CC BY 4.0 | ✓ | ✓ | ✓ |
| `acled` | ACLED | credentials + permission | restrictive (AI/ML/redistribution limits) | ✗ | ✗ | ✗ |
| `world_bank`, `imf`, `oecd`, `fred`, `un_comtrade` | economic APIs | planned | per source | — | — | — |

Permissions live in `source_permissions` and are enforced in code (`app/ai/evidence.py`, `app/ml/export.py`).

## Missing credentials / unsupported in the MVP

- **UCDP API** (`ucdpapi.pcr.uu.se`) requires an access token; GeoRisk uses the public bulk downloads instead.
- **ACLED**: the interface is defined but disabled; enable only with valid credentials and appropriate licence terms.
- **Anthropic / OpenAI**: set `AI_PROVIDER` and the matching key. Without them, every analysis endpoint returns `unavailable`.
- **FRED, UN Comtrade**: API keys will be needed when the economic module is implemented.

## Citations

- The GDELT Project, https://www.gdeltproject.org/. Leetaru, K. & Schrodt, P. (2013). *GDELT: Global Data on Events, Language,
  and Tone, 1979–2012.* ISA Annual Convention.
- Sundberg, R. & Melander, E. (2013). Introducing the UCDP Georeferenced Event Dataset. *Journal of Peace Research* 50(4).
  Davies, S., Pettersson, T., Sollenberg, M. & Öberg, M. (2025). Organized violence 1989–2024. *Journal of Peace Research* 62(4).
- Country reference data: ISO 3166, UN M49 and Gleditsch–Ward codes, compiled with `country_converter` (see
  `backend/scripts/build_country_seed.py`). Basemap: Natural Earth (public domain) via `world-atlas`.
