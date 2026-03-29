import sys
import socket
import ssl
import re
from urllib.parse import urlparse


def strip_html(text):
    """Remove HTML tags and decode common entities."""
    # Remove script and style blocks entirely
    text = re.sub(r'<(script|style)[^>]*>.*?</(script|style)>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    entities = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
        '&#39;': "'", '&nbsp;': ' ', '&mdash;': '--', '&ndash;': '-',
        '&laquo;': '<<', '&raquo;': '>>', '&hellip;': '...',
    }
    for entity, char in entities.items():
        text = text.replace(entity, char)
    # Remove numeric entities
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&[a-zA-Z]+;', '', text)
    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def decode_chunked(body):
    """Decode HTTP chunked transfer encoding."""
    result = []
    while body:
        line_end = body.find('\r\n')
        if line_end == -1:
            break
        size = int(body[:line_end].split(';')[0], 16)
        if size == 0:
            break
        chunk = body[line_end + 2: line_end + 2 + size]
        result.append(chunk)
        body = body[line_end + 2 + size + 2:]
    return ''.join(result)


def parse_url(url):
    """Parse URL into (scheme, host, port, path)."""
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
    """Make an HTTP GET request over a raw TCP socket. Follows redirects."""
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

            # Read full response
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()

        raw = b''.join(chunks).decode('utf-8', errors='replace')

        # Split headers from body
        if '\r\n\r\n' in raw:
            headers_part, body = raw.split('\r\n\r\n', 1)
        else:
            headers_part, body = raw, ''

        header_lines = headers_part.split('\r\n')
        status_line = header_lines[0]
        status_code = int(status_line.split(' ', 2)[1])

        headers = {}
        for line in header_lines[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        # Decode chunked transfer encoding
        if headers.get('transfer-encoding', '').lower() == 'chunked':
            body = decode_chunked(body)

        # Handle redirects
        if status_code in (301, 302, 303, 307, 308) and 'location' in headers:
            url = headers['location']
            continue

        return status_code, headers, body

    raise Exception(f"Too many redirects")


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
        search_term = ' '.join(args[1:])
        print(f"Search not yet implemented. Term: {search_term}")

    else:
        print(f"Unknown option: {args[0]}")
        show_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
