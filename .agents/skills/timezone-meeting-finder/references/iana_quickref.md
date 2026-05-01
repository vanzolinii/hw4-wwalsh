# IANA timezone quick reference

Users almost always say "Tokyo" or "EST" rather than `Asia/Tokyo`. Use this table to translate to a proper IANA zone before invoking the script. When in doubt, prefer the city-based zone over an abbreviation — `EST` and `PST` ignore DST, while `America/New_York` and `America/Los_Angeles` follow it.

## Common cities

| City / region | IANA zone |
|---|---|
| New York, Boston, Atlanta, Toronto | `America/New_York` |
| Chicago, Dallas, Mexico City | `America/Chicago` |
| Denver, Salt Lake City | `America/Denver` |
| Phoenix (no DST) | `America/Phoenix` |
| Los Angeles, San Francisco, Seattle, Vancouver | `America/Los_Angeles` |
| Honolulu | `Pacific/Honolulu` |
| Anchorage | `America/Anchorage` |
| São Paulo, Rio de Janeiro | `America/Sao_Paulo` |
| Buenos Aires | `America/Argentina/Buenos_Aires` |
| London, Edinburgh, Dublin* | `Europe/London` (*`Europe/Dublin` for Ireland) |
| Lisbon | `Europe/Lisbon` |
| Berlin, Paris, Madrid, Rome, Amsterdam, Stockholm, Warsaw | `Europe/Berlin` / `Europe/Paris` / `Europe/Madrid` / `Europe/Rome` (all CET/CEST) |
| Athens, Helsinki, Bucharest | `Europe/Athens` (EET/EEST) |
| Istanbul | `Europe/Istanbul` |
| Moscow | `Europe/Moscow` |
| Tel Aviv | `Asia/Jerusalem` |
| Dubai, Abu Dhabi | `Asia/Dubai` |
| Riyadh | `Asia/Riyadh` |
| Tehran | `Asia/Tehran` |
| Karachi | `Asia/Karachi` |
| Mumbai, Delhi, Bangalore, Chennai, Kolkata | `Asia/Kolkata` |
| Kathmandu | `Asia/Kathmandu` |
| Dhaka | `Asia/Dhaka` |
| Bangkok, Jakarta | `Asia/Bangkok` / `Asia/Jakarta` |
| Singapore, Kuala Lumpur, Manila | `Asia/Singapore` / `Asia/Kuala_Lumpur` / `Asia/Manila` |
| Hong Kong, Beijing, Shanghai, Taipei | `Asia/Hong_Kong` / `Asia/Shanghai` / `Asia/Taipei` |
| Seoul | `Asia/Seoul` |
| Tokyo, Osaka | `Asia/Tokyo` |
| Sydney, Melbourne, Canberra | `Australia/Sydney` |
| Brisbane (no DST) | `Australia/Brisbane` |
| Perth | `Australia/Perth` |
| Auckland | `Pacific/Auckland` |
| Johannesburg, Cape Town | `Africa/Johannesburg` |
| Lagos | `Africa/Lagos` |
| Nairobi | `Africa/Nairobi` |
| Cairo | `Africa/Cairo` |
| UTC / GMT | `UTC` |

## Half-hour and 45-minute offsets to remember

These are the zones that are *not* whole-hour offsets and trip up naive timezone math. Always use the IANA name, never an offset string:

- `Asia/Kolkata` — UTC+05:30 (all of India)
- `Asia/Kathmandu` — UTC+05:45 (Nepal)
- `Asia/Yangon` — UTC+06:30 (Myanmar)
- `Asia/Tehran` — UTC+03:30 (Iran, with DST)
- `Australia/Adelaide`, `Australia/Darwin` — UTC+09:30 / +10:30
- `Pacific/Marquesas` — UTC−09:30
- `Pacific/Chatham` — UTC+12:45 / +13:45 (Chatham Islands, NZ)

## Abbreviation traps

If a user says one of these, ask which they mean before assuming:

- `EST` could mean US Eastern Standard or Australian Eastern Standard. Prefer `America/New_York` or `Australia/Sydney`.
- `CST` could mean US Central, China Standard, or Cuba Standard. Prefer `America/Chicago`, `Asia/Shanghai`, or `America/Havana`.
- `IST` could mean India, Israel, or Ireland. Prefer `Asia/Kolkata`, `Asia/Jerusalem`, or `Europe/Dublin`.
- `BST` could mean British Summer Time or Bangladesh Standard. Prefer `Europe/London` or `Asia/Dhaka`.

When the user gives an unambiguous city, use the city-based IANA zone — DST handling is correct and historic offsets are accurate.
