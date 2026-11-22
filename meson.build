project('droiddesk', version: '0.3.0', meson_version: '>=0.60.0')

app_id = 'io.github.example.DroidDesk'

install_data(
  'src/droiddesk.py',
  install_dir: get_option('bindir'),
  rename: 'droiddesk',
  install_mode: 'rwxr-xr-x',
)
install_data(
  'src/droidlink_bridge.py',
  install_dir: get_option('bindir'),
)
install_data(
  'data/' + app_id + '.desktop',
  install_dir: get_option('datadir') / 'applications',
)
install_data(
  'data/' + app_id + '.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'data/' + app_id + '.metainfo.xml',
  install_dir: get_option('datadir') / 'metainfo',
)
