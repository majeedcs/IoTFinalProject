document.addEventListener("DOMContentLoaded", () => {
  const toggleBtn = document.getElementById("langToggle");
  const enElements = document.querySelectorAll(".lang-en");
  const frElements = document.querySelectorAll(".lang-fr");

  let currentLang = localStorage.getItem("language") || "en";

  const applyLanguage = (lang) => {
    if (lang === "en") {
      enElements.forEach((el) => el.classList.remove("d-none"));
      frElements.forEach((el) => el.classList.add("d-none"));
      toggleBtn.textContent = "FR";
    } else {
      // Show French, Hide English
      enElements.forEach((el) => el.classList.add("d-none"));
      frElements.forEach((el) => el.classList.remove("d-none"));
      toggleBtn.textContent = "EN";
    }
  };

  applyLanguage(currentLang);

  toggleBtn.addEventListener("click", () => {
    currentLang = currentLang === "en" ? "fr" : "en";
    localStorage.setItem("language", currentLang);
    applyLanguage(currentLang);
  });
});
