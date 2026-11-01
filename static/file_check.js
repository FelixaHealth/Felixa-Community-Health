(() => {const d = document.currentScript.dataset, maxFileMb = d.maxFileMb, maxUploadMb = d.maxUploadMb, maxFile = maxFileMb * 1e6, maxTotal = maxUploadMb * 1e6;
document.querySelectorAll('input[type=file]').forEach(input => input.addEventListener('change', () => {
  const files = [...input.files], big = files.filter(f => f.size > maxFile).map(f => f.name), total = files.reduce((n, f) => n + f.size, 0);
  input.setCustomValidity(big.length ? `Over ${maxFileMb} MB, too large to import: ${big.join(', ')}.` : total > maxTotal ? `More than ${maxUploadMb} MB in all: choose fewer files at a time.` : '');
  if (!input.validity.valid) input.reportValidity();}));})();
