import * as sass from 'sass';
import {readFileSync, writeFileSync} from 'node:fs';
const result = sass.compile('scss/sopds.scss', {
  loadPaths: ['node_modules/foundation-sites/scss', 'node_modules/motion-ui/src'],
  quietDeps: true,
  style: 'compressed', sourceMap: false, charset: false,
  silenceDeprecations: ['import', 'global-builtin', 'slash-div', 'color-functions', 'if-function'],
});
const css = result.css + '\n';
for (const path of ['../../static/css/sopds.css', '../../sopds_web_backend/static/css/sopds.css']) {
  if (process.argv.includes('--check')) {
    if (readFileSync(path, 'utf8') !== css) throw new Error('Rebuild CSS: ' + path);
  } else writeFileSync(path, css);
}
console.log(process.argv.includes('--check') ? 'CSS is reproducible' : 'Built SOPDS CSS');
