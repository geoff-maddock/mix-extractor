/* mix-extractor web UI — table sorting */

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.sortable-table').forEach(table => {
    const headers = table.querySelectorAll('th.sortable');
    headers.forEach(th => {
      th.addEventListener('click', () => {
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const isAsc = th.classList.contains('sort-asc');

        // Reset all headers
        headers.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
        th.classList.add(isAsc ? 'sort-desc' : 'sort-asc');

        const idx = th.cellIndex;
        rows.sort((a, b) => {
          const av = a.cells[idx]?.textContent.trim().toLowerCase() || '';
          const bv = b.cells[idx]?.textContent.trim().toLowerCase() || '';
          return isAsc ? bv.localeCompare(av) : av.localeCompare(bv);
        });

        rows.forEach(r => tbody.appendChild(r));
      });
    });
  });
});
