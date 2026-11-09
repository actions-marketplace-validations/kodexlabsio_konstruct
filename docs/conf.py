"""Sphinx configuration for the Konstruct documentation (https://kodexlabsio.github.io/konstruct/)."""

from konstruct import __version__

project = 'Konstruct'
author = 'Kodex Labs Ltd.'
copyright = '2026, Kodex Labs Ltd.'
version = release = __version__
language = 'en_GB'

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinxarg.ext',
    'sphinx_copybutton',
    'sphinxcontrib.mermaid',
]

source_suffix = {'.md': 'markdown', '.rst': 'restructuredtext'}
exclude_patterns = ['_build', 'examples', 'Thumbs.db', '.DS_Store']
myst_enable_extensions = ['colon_fence', 'deflist', 'attrs_inline']
myst_heading_anchors = 3
myst_fence_as_directive = ['mermaid']
# sphinx-argparse registers a domain without resolve_any_xref, which MyST warns about for every link.
suppress_warnings = ['myst.domains']

autodoc_member_order = 'bysource'
autodoc_typehints = 'description'
autodoc_default_options = {'members': True, 'undoc-members': False, 'show-inheritance': False}

copybutton_prompt_text = r'\$ '
copybutton_prompt_is_regexp = True

html_theme = 'sphinx_rtd_theme'
html_title = f'Konstruct {version}'
html_logo = 'images/logo-icon.svg'
html_favicon = 'images/logo-icon.svg'
html_static_path = ['_static']
html_css_files = ['konstruct.css']
html_show_sourcelink = False
html_theme_options = {
    'style_nav_header_background': '#1F2A2E',
    'collapse_navigation': False,
    'navigation_depth': 3,
    'prev_next_buttons_location': 'bottom',
}
html_context = {
    'display_github': True,
    'github_user': 'kodexlabsio',
    'github_repo': 'konstruct',
    'github_version': 'main',
    'conf_py_path': '/docs/',
}
