import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {
  getVideoMetadata,
  renderMedia,
  selectComposition,
} from '@remotion/renderer';

const here = path.dirname(fileURLToPath(import.meta.url));
const [propsPath, outputPath, publicDir, browserExecutable] =
  process.argv.slice(2);

if (!propsPath || !outputPath || !publicDir) {
  throw new Error(
    'Usage: node render.mjs <props.json> <output.mp4> <public-dir> [browser]',
  );
}

const inputProps = JSON.parse(fs.readFileSync(propsPath, 'utf8'));
const serveUrl = await bundle({
  entryPoint: path.join(here, 'src', 'index.jsx'),
  publicDir,
  onProgress: () => undefined,
});
const browser = browserExecutable || undefined;
const composition = await selectComposition({
  serveUrl,
  id: 'UGCFinal',
  inputProps,
  browserExecutable: browser,
  logLevel: 'warn',
});

let lastReported = -1;
await renderMedia({
  composition,
  serveUrl,
  codec: 'h264',
  audioCodec: 'aac',
  pixelFormat: 'yuv420p',
  crf: 18,
  x264Preset: 'medium',
  outputLocation: outputPath,
  inputProps,
  browserExecutable: browser,
  overwrite: true,
  concurrency: 2,
  logLevel: 'warn',
  onProgress: ({progress}) => {
    const percent = Math.floor(progress * 100);
    if (percent >= lastReported + 5) {
      lastReported = percent;
      process.stdout.write(`render_progress=${percent}\n`);
    }
  },
});

const metadata = await getVideoMetadata(outputPath);
fs.writeFileSync(
  `${outputPath}.metadata.json`,
  JSON.stringify(metadata, null, 2),
  'utf8',
);
