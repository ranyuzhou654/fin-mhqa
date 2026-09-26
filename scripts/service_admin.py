"""Create service users. The API key is printed once; only its SHA-256 hash is stored."""
import argparse
from finmhqa.service.store import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='data/service/service.db')
    p.add_argument('--name', required=True)
    p.add_argument('--admin', action='store_true', help='may read /metrics')
    p.add_argument('--max-concurrent', type=int, default=2)
    p.add_argument('--max-queued', type=int, default=10)
    p.add_argument('--rate-per-min', type=int, default=20)
    p.add_argument('--daily-tokens', type=int, default=5_000_000)
    a = p.parse_args()
    uid, key = Store(a.db).create_user(a.name, a.admin, a.max_concurrent, a.max_queued, a.rate_per_min, a.daily_tokens)
    print(f'user {a.name} ({uid}) created. API key (shown once, store it safely):\n{key}')


if __name__ == '__main__':
    main()
