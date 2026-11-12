// Story page: story.html?slug=<slug>
// Shows the story, big ideas, words to know, the quiz and the source credit.

import { el, fetchJson, formatDate, isValidSlug, paragraphs, safeUrl } from "./common.js";

const root = document.getElementById("story");
const LETTERS = ["A", "B", "C", "D", "E"];

function showMessage(text) {
  root.replaceChildren(
    el("p", { class: "notice", text }),
    el("p", {}, el("a", { href: "./" }, "← Back to all stories")),
  );
}

// --- Story sections ----------------------------------------------------------

function header(story, hasQuiz) {
  const meta = [
    story.topic && el("span", { class: "tag", text: story.topic }),
    story.published_at && el("span", { text: formatDate(story.published_at) }),
    story.reading_level != null &&
      el("span", { text: `Reading level: grade ${Math.round(Number(story.reading_level))}` }),
  ];
  return el("header", {},
    el("a", { class: "back", href: "./" }, "← All stories"),
    el("h1", { text: story.kid_title }),
    el("div", { class: "meta" }, meta),
    hasQuiz && el("a", { class: "button quiz-jump", href: "#quiz-heading" }, "Take the quiz ↓"),
  );
}

function figure(story) {
  const src = safeUrl(story.image_url);
  if (!src) return null;
  return el("figure", {},
    el("img", { src, alt: story.image_alt || "", referrerpolicy: "no-referrer" }),
    story.image_credit && el("figcaption", { text: `Image: ${story.image_credit}` }),
  );
}

function definitionBox(title, items, termKey, textKey) {
  const rows = (Array.isArray(items) ? items : []).filter((i) => i && i[termKey] && i[textKey]);
  if (rows.length === 0) return null;
  return el("section", {},
    el("h2", { text: title }),
    el("div", { class: "box" },
      el("dl", {}, rows.flatMap((i) => [el("dt", { text: i[termKey] }), el("dd", { text: i[textKey] })])),
    ),
  );
}

function creditBox(story) {
  const link = safeUrl(story.source_url);
  const title = story.source_title || "the original article";
  const publisher = story.publisher_name || "the original publisher";
  return el("section", { "aria-labelledby": "credit-heading" },
    el("h2", { id: "credit-heading", text: "Where this story comes from" }),
    el("div", { class: "box credit-box" },
      el("p", {},
        "Based on ",
        link ? el("a", { href: link, rel: "noopener noreferrer", target: "_blank" }, `“${title}”`) : `“${title}”`,
        `, ${publisher}.`,
      ),
      el("p", { text: `Summary and quiz written by AI and checked by a person. Not reviewed or endorsed by ${publisher.startsWith("NASA") ? "NASA" : publisher}.` }),
      story.image_credit && el("p", { text: `Image: ${story.image_credit}` }),
    ),
  );
}

// --- Quiz --------------------------------------------------------------------

function quiz(questions) {
  const valid = (Array.isArray(questions) ? questions : []).filter(
    (q) => q && q.prompt && Array.isArray(q.options) &&
      Number.isInteger(q.correct_index) && q.correct_index >= 0 && q.correct_index < q.options.length,
  );
  if (valid.length === 0) return null;

  const section = el("section", { "aria-labelledby": "quiz-heading" });
  const score = el("p", { class: "score", role: "status", "aria-live": "polite" });
  let answered = 0;
  let correct = 0;

  function updateScore() {
    if (answered === 0) {
      score.textContent = `Answer all ${valid.length} questions to see your score.`;
      return;
    }
    if (answered < valid.length) {
      score.textContent = `${correct} correct so far · ${valid.length - answered} to go`;
      return;
    }
    const cheer = correct === valid.length ? "Perfect score! 🌍" : correct >= valid.length / 2 ? "Nice work!" : "Good try! Read the story again and have another go.";
    score.textContent = `You got ${correct} out of ${valid.length}. ${cheer}`;
    retry.hidden = false;
  }

  function question(q, n) {
    const feedback = el("p", { class: "feedback", "aria-live": "polite" });
    const buttons = q.options.map((option, i) =>
      el("button", { type: "button", class: "option", onclick: () => choose(i) },
        el("span", { class: "letter", "aria-hidden": "true", text: LETTERS[i] }),
        el("span", { text: option }),
        el("span", { class: "mark", "aria-hidden": "true" }),
      ),
    );

    function choose(picked) {
      const right = picked === q.correct_index;
      answered += 1;
      if (right) correct += 1;

      buttons.forEach((b, i) => {
        b.disabled = true;
        if (i === q.correct_index) {
          b.classList.add("correct");
          b.querySelector(".mark").textContent = "✓";
          b.setAttribute("aria-label", `${q.options[i]} (correct answer)`);
        } else if (i === picked) {
          b.classList.add("wrong");
          b.querySelector(".mark").textContent = "✗";
          b.setAttribute("aria-label", `${q.options[i]} (your answer, not correct)`);
        }
      });

      feedback.replaceChildren(
        right
          ? el("strong", { class: "good", text: "Correct! " })
          : el("strong", { class: "bad", text: `Not quite. The answer is ${LETTERS[q.correct_index]}. ` }),
        q.explanation || "",
      );
      updateScore();
    }

    return el("fieldset", { class: "question" },
      el("legend", { text: `${n + 1}. ${q.prompt}` }),
      el("div", { class: "options" }, buttons),
      feedback,
    );
  }

  const list = el("div");
  const retry = el("button", { type: "button", class: "button", hidden: true, onclick: reset }, "Try again");

  function reset() {
    answered = 0;
    correct = 0;
    retry.hidden = true;
    list.replaceChildren(...valid.map(question));
    updateScore();
    list.querySelector(".option")?.focus();
  }

  section.append(
    el("h2", { id: "quiz-heading", text: "Quiz time" }),
    el("p", { class: "intro", text: "Pick one answer for each question. Everything you need is in the story above." }),
    list,
    el("div", { class: "box score-box" }, score, retry),
  );
  list.replaceChildren(...valid.map(question));
  updateScore();
  return section;
}

// --- Page --------------------------------------------------------------------

async function main() {
  const slug = new URLSearchParams(location.search).get("slug");
  if (!isValidSlug(slug)) {
    showMessage("We couldn't find that story.");
    return;
  }

  let story;
  try {
    story = await fetchJson(`data/stories/${slug}.json`);
  } catch (err) {
    console.error(err);
    showMessage("We couldn't find that story. It may have moved.");
    return;
  }

  document.title = `${story.kid_title} · KidNews Earth`;
  const quizSection = quiz(story.questions);
  root.replaceChildren(
    header(story, Boolean(quizSection)),
    figure(story),
    el("p", { class: "lead", text: story.summary }),
    ...paragraphs(story.body).map((p) => el("p", { text: p })),
    definitionBox("Big ideas", story.concepts, "name", "explanation"),
    definitionBox("Words to know", story.glossary, "term", "definition"),
    quizSection,
    creditBox(story),
  );
  root.removeAttribute("aria-busy");
}

main();
