import sys
import socket
import ssl
import re
import os
import json
import hashlib
import time
from urllib.parse import urlparse, quote_plus, unquote_plus

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', '.cache')

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def strip_html(text):
    text = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    entities = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
        '&#39;': "'", '&nbsp;': ' ', '&mdash;': '--', '&ndash;': '-',
        '&laquo;': '<<', '&raquo;': '>>', '&hellip;': '...',
    }
    for entity, char in entities.items():
        text = text.replace(entity, char)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&[a-zA-Z]+;', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def cache_key(url):
    return hashlib.md5(url.encode()).hexdigest()


def cache_load(url):
    path = os.path.join(CACHE_DIR, cache_key(url))
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        entry = json.load(f)
    if time.time() > entry['expires']:
        return None
    return entry['headers'], entry['body']


def cache_save(url, headers, body):
    max_age = 0
    cc = headers.get('cache-control', '')
    for part in cc.split(','):
        part = part.strip()
        if part.startswith('max-age='):
            try:
                max_age = int(part[8:])
            except ValueError:
                pass
    if max_age == 0 and 'expires' in headers:
        try:
            from email.utils import parsedate_to_datetime
            expires_dt = parsedate_to_datetime(headers['expires'])
            max_age = max(0, int(expires_dt.timestamp() - time.time()))
        except Exception:
            pass
    if max_age <= 0:
        max_age = 3600
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, cache_key(url))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'headers': headers, 'body': body, 'expires': time.time() + max_age}, f)


def decode_chunked(data: bytes) -> bytes:
    result = bytearray()
    while data:
        line_end = data.find(b'\r\n')
        if line_end == -1:
            break
        size_str = data[:line_end].split(b';')[0].strip()
        if not size_str:
            data = data[line_end + 2:]
            continue
        try:
            size = int(size_str, 16)
        except ValueError:
            break
        if size == 0:
            break
        result.extend(data[line_end + 2: line_end + 2 + size])
        data = data[line_end + 2 + size + 2:]
    return bytes(result)


def parse_url(url):
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'http://' + url
    parsed = urlparse(url)
    scheme = parsed.scheme
    host = parsed.hostname
    port = parsed.port or (443 if scheme == 'https' else 80)
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    return scheme, host, port, path


def http_get(url, max_redirects=5):
    cached = cache_load(url)
    if cached:
        return 200, cached[0], cached[1]

    for _ in range(max_redirects):
        scheme, host, port, path = parse_url(url)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)

        try:
            sock.connect((host, port))

            if scheme == 'https':
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)

            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Connection: close\r\n"
                f"User-Agent: go2web/1.0\r\n"
                f"Accept: text/html,application/json\r\n"
                f"\r\n"
            )
            sock.sendall(request.encode())

            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()

        raw = b''.join(chunks)

        if b'\r\n\r\n' in raw:
            headers_raw, body_raw = raw.split(b'\r\n\r\n', 1)
        else:
            headers_raw, body_raw = raw, b''

        headers_text = headers_raw.decode('utf-8', errors='replace')
        header_lines = headers_text.split('\r\n')
        status_line = header_lines[0]
        status_code = int(status_line.split(' ', 2)[1])

        headers = {}
        for line in header_lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        if headers.get('transfer-encoding', '').lower() == 'chunked':
            body_raw = decode_chunked(body_raw)

        body = body_raw.decode('utf-8', errors='replace')

        if status_code in (301, 302, 303, 307, 308) and 'location' in headers:
            url = headers['location']
            continue

        cache_save(url, headers, body)
        return status_code, headers, body

    raise Exception('Too many redirects')


def get_search_results(search_term):
    query = quote_plus(search_term)
    _, _, body = http_get(f"https://html.duckduckgo.com/html/?q={query}")

    raw = re.findall(
        r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        body,
        re.DOTALL | re.IGNORECASE,
    )

    results = []
    for href, title in raw:
        if len(results) >= 10:
            break
        title = strip_html(title).strip()
        uddg = re.search(r'[?&]uddg=([^&]+)', href)
        if uddg:
            href = unquote_plus(uddg.group(1))
        if title and href:
            results.append((title, href))
    return results


def handle_search(search_term, open_index=None):
    results = get_search_results(search_term)

    if not results:
        print("No results found.")
        return

    if open_index is not None:
        if open_index < 1 or open_index > len(results):
            print(f"Invalid index. Choose between 1 and {len(results)}.")
            return
        _, url = results[open_index - 1]
        handle_url(url)
        return

    for i, (title, href) in enumerate(results, 1):
        print(f"{i}. {title}")
        print(f"   {href}\n")


def handle_url(url):
    _, headers, body = http_get(url)
    content_type = headers.get('content-type', '')

    if 'json' in content_type:
        print(body)
    else:
        print(strip_html(body))


def show_help():
    print("Usage:")
    print("  go2web -u <URL>          Make an HTTP request to the URL and print the response")
    print("  go2web -s <search-term>  Search and print top 10 results")
    print("  go2web -h                Show this help")


def main():
    args = sys.argv[1:]

    if not args or args[0] == '-h':
        show_help()
        return

    if args[0] == '-u':
        if len(args) < 2:
            print("Error: -u requires a URL argument")
            sys.exit(1)
        handle_url(args[1])

    elif args[0] == '-s':
        if len(args) < 2:
            print("Error: -s requires a search term")
            sys.exit(1)
        rest = args[1:]
        open_index = None
        if rest and rest[-1].isdigit():
            open_index = int(rest[-1])
            rest = rest[:-1]
        if not rest:
            print("Error: -s requires a search term")
            sys.exit(1)
        search_term = ' '.join(rest)
        handle_search(search_term, open_index)

    else:
        print(f"Unknown option: {args[0]}")
        show_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
