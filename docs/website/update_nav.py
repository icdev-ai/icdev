"""Update WordPress navigation menu for icdev.ai."""
import os
import sys
import xmlrpc.client

_user = os.getenv("WP_USERNAME", "pulse-bot")
_pass = os.getenv("WP_PASSWORD")
if not _pass:
    sys.exit("Error: Set WP_PASSWORD in .env before running this script.")

wp = xmlrpc.client.ServerProxy('https://icdev.ai/xmlrpc.php')
auth = (1, _user, _pass)

MENU_ID = 13  # Primary Menu

# Get existing menu items to delete them
posts = wp.wp.getPosts(*auth, {
    'post_type': 'nav_menu_item',
    'number': 50,
})
menu_items = [p for p in posts if str(MENU_ID) in str(p.get('terms', []))]
print(f'Existing menu items: {len(menu_items)}')

# Delete old menu items
for item in menu_items:
    try:
        wp.wp.deletePost(*auth, item['post_id'])
        print(f'  Deleted: {item["post_title"]}')
    except Exception as e:
        print(f'  Could not delete {item["post_id"]}: {e}')

# Create new menu items
# WordPress menu items are nav_menu_item post type with specific meta
new_items = [
    {'title': 'Home', 'url': 'https://icdev.ai/', 'order': 1},
    {'title': 'Platform', 'url': 'https://icdev.ai/platform/', 'order': 2},
    {'title': 'Solutions', 'url': 'https://icdev.ai/solutions/', 'order': 3},
    {'title': 'Blog', 'url': 'https://icdev.ai/blog/', 'order': 4},
    {'title': 'About', 'url': 'https://icdev.ai/about/', 'order': 5},
    {'title': 'Contact', 'url': 'https://icdev.ai/contact/', 'order': 6},
    {'title': 'GitHub', 'url': 'https://github.com/icdev-ai/icdev', 'order': 7},
]

for item in new_items:
    try:
        item_id = wp.wp.newPost(*auth, {
            'post_type': 'nav_menu_item',
            'post_status': 'publish',
            'post_title': item['title'],
            'menu_order': item['order'],
            'custom_fields': [
                {'key': '_menu_item_type', 'value': 'custom'},
                {'key': '_menu_item_url', 'value': item['url']},
                {'key': '_menu_item_menu_item_parent', 'value': '0'},
                {'key': '_menu_item_object', 'value': 'custom'},
                {'key': '_menu_item_object_id', 'value': '0'},
                {'key': '_menu_item_target', 'value': '_blank' if 'github' in item['url'] else ''},
            ],
            'terms_names': {'nav_menu': ['Primary Menu']},
        })
        print(f'  Created: {item["title"]} (ID={item_id})')
    except Exception as e:
        print(f'  Error creating {item["title"]}: {e}')

print('\nNavigation updated. Purge LiteSpeed cache to see changes.')
