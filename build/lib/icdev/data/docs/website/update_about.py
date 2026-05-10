"""Update About page on icdev.ai with matching theme."""
import os
import sys
import xmlrpc.client

_user = os.getenv("WP_USERNAME", "pulse-bot")
_pass = os.getenv("WP_PASSWORD")
if not _pass:
    sys.exit("Error: Set WP_PASSWORD in .env before running this script.")

wp = xmlrpc.client.ServerProxy('https://icdev.ai/xmlrpc.php')
auth = (1, _user, _pass)

with open('docs/website/about.html', 'r', encoding='utf-8') as f:
    html = f.read()

wp.wp.editPost(*auth, 1228, {
    'post_title': 'About ICDEV™',
    'post_content': html,
})
print('About page updated')
