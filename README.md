# Montréal Independent Performing Arts Calendar

An automatically refreshed calendar of independent theatre, contemporary dance, performance, and interdisciplinary live art in Montréal.

Sources currently include the official schedules of La Chapelle Scènes Contemporaines, Tangente, Agora de la danse, and MAI. The feed uses individual published performance times, `America/Toronto`, stable event identifiers, deduplication, and two-run cancellation handling.

Website: https://k10sj02.github.io/montreal-performing-arts-calendar/

Subscription: https://k10sj02.github.io/montreal-performing-arts-calendar/montreal-performing-arts.ics

## Local use

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
montreal-performing-arts-calendar
```
