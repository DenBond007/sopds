import {chromium} from '../assets/sopds-sass/node_modules/playwright-core/index.mjs';
import assert from 'node:assert/strict';
const base = process.env.SOPDS_E2E_URL;
const username = process.env.SOPDS_E2E_USERNAME;
const password = process.env.SOPDS_E2E_PASSWORD;
if (!base || !username || !password) throw new Error('Use the Django isolated browser test runner');
const browser = await chromium.launch(process.env.SOPDS_BROWSER_EXECUTABLE ?
  {executablePath:process.env.SOPDS_BROWSER_EXECUTABLE} : {});
const page = await browser.newPage({viewport:{width:1280,height:900}});
const failures = [];
page.on('pageerror', error => failures.push(error.message));
try {
  await page.goto(base + '/web/login/');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await Promise.all([page.waitForURL(url => !url.pathname.endsWith('/login/')), page.locator('form').filter({has:page.locator('[name=password]')}).locator('[type=submit]').click()]);
  const terms = 'War & Peace +100%';
  await page.goto(base + '/web/search/books/?' + new URLSearchParams({searchtype:'m',searchterms:terms}));
  await page.locator('.pagination-next a').click();
  assert.equal(new URL(page.url()).searchParams.get('searchterms'), terms);
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.getByRole('link',{name:'Хочу прочитать',exact:true}).click();
  await page.getByRole('link',{name:'Продолжить чтение',exact:true}).click();
  await page.waitForFunction(() => document.getElementById('reading-message').textContent.startsWith('Позиция:'));
  await page.waitForTimeout(200);
  const bookFrame = page.frameLocator('#reading-frame');
  await bookFrame.locator('body').evaluate(() => {
    const el = document.scrollingElement;
    window.scrollTo(0, (el.scrollHeight - el.clientHeight) * .4);
  });
  await page.waitForFunction(() => document.getElementById('reading-message').textContent.startsWith('Сохранено:'));
  let position = await page.locator('#reading-frame').evaluate(async frame =>
    (await (await fetch(frame.dataset.stateUrl)).json()).position);
  assert.ok(position > 3500 && position < 4500, 'Saved scroll position must be about 40%');
  await page.reload();
  await page.waitForFunction(() => document.getElementById('reading-message').textContent.startsWith('Позиция:'));
  const restored = await bookFrame.locator('body').evaluate(() => {
    const el = document.scrollingElement;
    return el.scrollTop / (el.scrollHeight - el.clientHeight);
  });
  assert.ok(restored > .35 && restored < .45, 'Reload must restore the saved position');
  await page.setViewportSize({width:390,height:844});
  await page.goto(base + '/web/search/books/?searchtype=m&reading_status=reading');
  await page.getByRole('button',{name:'Сохранить',exact:true}).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2), 'Search must fit a mobile viewport');
  if (process.env.SOPDS_E2E_SCREENSHOT) await page.screenshot({path:process.env.SOPDS_E2E_SCREENSHOT,fullPage:true});
  assert.deepEqual(failures, [], 'Browser JavaScript errors');
  console.log('Browser smoke passed: login, encoded pagination, personal list, scroll save/restore, mobile layout');
} finally {
  await browser.close();
}
