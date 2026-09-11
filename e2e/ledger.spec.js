const { test, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

test('収支の登録から集計、編集、検索、CSV出力、削除まで動作する', async ({ page }) => {
  const browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });

  await page.goto('/');
  await expect(page).toHaveTitle('ダッシュボード｜tsumugi');
  await expect(page.locator('#syncStatus')).toHaveText('保存済み · このパソコン');

  await page.getByRole('link', { name: '収支台帳' }).click();
  await expect(page.locator('#pageTitle')).toHaveText('収支台帳');
  await page.locator('#openEntry').click();

  const dialog = page.locator('#entryDialog');
  const title = dialog.locator('input[name="title"]');
  await expect(dialog).toBeVisible();
  await dialog.locator('#saveEntry').click();
  await expect(dialog).toBeVisible();
  await expect.poll(() => title.evaluate((element) => element.validity.valid)).toBe(false);

  await title.fill('E2E 食費テスト');
  await dialog.locator('input[name="amount"]').fill('1234');
  await dialog.locator('input[name="date"]').fill('2026-09-08');
  await dialog.locator('textarea[name="note"]').fill('Playwrightで登録');
  await dialog.locator('#imageInput').setInputFiles({ name: 'receipt.png', mimeType: 'image/png', buffer: png });
  await expect(dialog.locator('#imagePreview')).toBeVisible();
  await dialog.locator('#saveEntry').click();

  await expect(dialog).toBeHidden();
  await expect(page.locator('#toast')).toHaveText('収支を追加しました');
  const row = page.getByRole('row').filter({ hasText: 'E2E 食費テスト' });
  await expect(row).toContainText('−¥1,234');
  await expect(row).toContainText('Playwrightで登録');
  const photoButton = row.getByRole('button', { name: 'E2E 食費テストの画像を表示' });
  await expect(photoButton).toBeVisible();
  await photoButton.click();
  const imageDialog = page.locator('#imageDialog');
  await expect(imageDialog).toBeVisible();
  await expect(imageDialog.locator('#fullImage')).toHaveAttribute('src', /\/api\/transactions\/\d+\/image/);
  await imageDialog.getByRole('button', { name: '閉じる' }).click();

  const downloadPromise = page.waitForEvent('download');
  await page.locator('#exportFiltered').click();
  const download = await downloadPromise;
  const csvPath = await download.path();
  const csv = await fs.readFile(csvPath);
  expect([...csv.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  expect(csv.toString('utf8')).toContain('E2E 食費テスト');

  await row.getByRole('button', { name: 'E2E 食費テストを編集' }).click();
  await expect(dialog.locator('#imagePreview')).toBeVisible();
  await dialog.locator('input[name="amount"]').fill('2345');
  await dialog.locator('#removeImage').click();
  await dialog.locator('#saveEntry').click();
  await expect(dialog).toBeHidden();
  await expect(row).toContainText('−¥2,345');
  await expect(photoButton).toHaveCount(0);

  await page.locator('#searchInput').fill('存在しない記録');
  await expect(page.locator('#ledgerTable')).toContainText('条件に合う記録がありません');
  await page.locator('#clearFilters').click();
  await expect(row).toBeVisible();

  await page.getByRole('link', { name: 'ダッシュボード' }).click();
  await expect(page.locator('#balanceValue')).toHaveText('−¥2,345');
  await expect(page.locator('#expenseValue')).toHaveText('¥2,345');
  await expect(page.locator('#categoryChart')).toContainText('食費');

  await page.getByRole('link', { name: '月次レポート' }).click();
  await expect(page.locator('#reportHighlights')).toContainText('¥2,345');
  await expect(page.locator('#reportTable')).toContainText('食費');

  await page.getByRole('link', { name: '設定・データ管理' }).click();
  await page.locator('#themeSetting').selectOption('dark');
  await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');

  await page.getByRole('link', { name: '収支台帳' }).click();
  await row.getByRole('button', { name: 'E2E 食費テストを削除' }).click();
  const deleteDialog = page.locator('#deleteDialog');
  await expect(deleteDialog).toContainText('E2E 食費テスト');
  await deleteDialog.getByRole('button', { name: '削除する' }).click();
  await expect(deleteDialog).toBeHidden();
  await expect(page.locator('#ledgerTable')).toContainText('この月の記録はまだありません');

  expect(browserErrors).toEqual([]);
});

test('モバイル幅でも主要操作が表示され、ページ全体が横にはみ出さない', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#transactions');

  await expect(page.locator('#pageTitle')).toHaveText('収支台帳');
  await expect(page.locator('#openEntry')).toBeVisible();
  await expect(page.locator('#searchInput')).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.viewport);

  await page.getByRole('link', { name: '月次レポート' }).click();
  await expect(page.locator('#pageTitle')).toHaveText('月次レポート');
  await page.getByRole('link', { name: '設定・データ管理' }).click();
  await expect(page.locator('#pageTitle')).toHaveText('設定・データ管理');
});
