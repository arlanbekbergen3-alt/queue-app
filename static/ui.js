function plural(count, one, few, many) {
  const n = Math.abs(Math.trunc(count));
  const last = n % 10;
  const tail = n % 100;
  if (last === 1 && tail !== 11) return one;
  if (last >= 2 && last <= 4 && (tail < 12 || tail > 14)) return few;
  return many;
}

function sessionName(title, group) {
  if (!group || title.toLowerCase().includes(group.toLowerCase())) return title;
  return title + " · " + group;
}

function bankNote(assignment) {
  if (!assignment || assignment.question_count >= assignment.questions_per_submission) return "";
  return "В банке " + counted(assignment.question_count, "вопрос", "вопроса", "вопросов") +
    ", а на сдачу нужно " + assignment.questions_per_submission + ": каждый студент получит " +
    assignment.question_count + ". Добавьте вопросы в банк.";
}

function counted(count, one, few, many) {
  return count + " " + plural(count, one, few, many);
}

function pointsLabel(count) {
  return counted(count, "балл", "балла", "баллов");
}
