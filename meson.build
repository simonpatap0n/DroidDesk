project('droiddesk', version: '0.2.1', meson_version: '>=0.60.0')

app_id = 'io.github.example.DroidDesk'

install_data(
  'src/droiddesk.py',
  install_dir: get_option('bindir'),
  rename: 'droiddesk',
  install_mode: 'rwxr-xr-x',
)
install_data(
  'src/droiddesk_tray.py',
  install_dir: get_option('bindir'),
  rename: 'droiddesk-tray',
  install_mode: 'rwxr-xr-x',
)
install_data(
  'src/droidlink_bridge.py',
  install_dir: get_option('bindir'),
)
configure_file(
  input: 'data/' + app_id + '.desktop.in',
  output: app_id + '.desktop',
  configuration: {'BINDIR': get_option('prefix') / get_option('bindir')},
  install_dir: get_option('datadir') / 'applications',
)
install_data(
  'data/' + app_id + '.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps',
  rename: 'droiddesk.svg',
)
install_data(
  'data/' + app_id + '.metainfo.xml',
  install_dir: get_option('datadir') / 'metainfo',
)
