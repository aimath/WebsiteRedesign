// When a source_program is selected in the VideoProgram admin, fetch its
// title and start_date and pre-fill the corresponding fields — only if they
// are currently empty, so existing values are never overwritten.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const programField = document.getElementById("id_source_program");
    if (!programField) return;

    programField.addEventListener("change", function () {
      const programId = this.value;
      if (!programId) return;

      fetch(`/admin/programs/program/${programId}/change/`, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((r) => r.text())
        .then((html) => {
          const doc = new DOMParser().parseFromString(html, "text/html");

          const remoteTitle = doc.getElementById("id_title")?.value;
          const remoteDate = doc.getElementById("id_start_date")?.value;

          const localTitle = document.getElementById("id_title");
          const localDate = document.getElementById("id_start_date");

          if (localTitle && !localTitle.value && remoteTitle) {
            localTitle.value = remoteTitle;
          }
          if (localDate && !localDate.value && remoteDate) {
            localDate.value = remoteDate;
          }
        })
        .catch(() => {});
    });
  });
})();
