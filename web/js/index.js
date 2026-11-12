// Home page: one card per published story, newest first.

import { el, fetchJson, formatDate, isValidSlug, safeUrl } from "./common.js";

const list = document.getElementById("stories");
const status = document.getElementById("status");

function card(story) {
  const image = safeUrl(story.image_url);
  return el(
    "li",
    { class: "card" },
    el(
      "a",
      { class: "card-link", href: `story.html?slug=${encodeURIComponent(story.slug)}` },
      image &&
        el("div", { class: "media" },
          el("img", { src: image, alt: "", loading: "lazy", referrerpolicy: "no-referrer" })),
      el(
        "div",
        { class: "content" },
        story.topic && el("span", { class: "tag", text: story.topic }),
        el("h2", { text: story.kid_title }),
        el("p", { text: story.summary }),
        story.published_at && el("p", { class: "credit", text: formatDate(story.published_at) }),
        story.image_credit && el("p", { class: "credit", text: `Image: ${story.image_credit}` }),
      ),
    ),
  );
}

async function main() {
  try {
    const data = await fetchJson("data/index.json");
    const stories = (data.stories || []).filter((s) => isValidSlug(s.slug) && s.kid_title);
    if (stories.length === 0) {
      status.textContent = "No stories yet. Check back soon!";
      return;
    }
    status.remove();
    list.append(...stories.map(card));
  } catch (err) {
    console.error(err);
    status.textContent = "Sorry, the stories couldn't be loaded. Please try again later.";
  }
}

main();
