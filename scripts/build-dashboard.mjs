import { mkdir, readFile, writeFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const template = await readFile(new URL('src/survai/dashboard.html', root), 'utf8');
const demo = await readFile(new URL('scripts/hosted-demo.js', root), 'utf8');
const marker = '<!-- Hosted demo script -->';
if (!template.includes(marker)) throw new Error('Dashboard template is missing its hosted demo marker');
await mkdir(new URL('dist/', root), { recursive: true });
await writeFile(new URL('dist/index.html', root), template.replace(marker, () => `<script>\n${demo}\n</script>`));
