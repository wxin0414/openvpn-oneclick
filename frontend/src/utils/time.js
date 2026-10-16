export const formatTimeZh = (time) => {
  if (!time) return "";
  const d = new Date(time);
  if (Number.isNaN(d.getTime())) return time;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .format(d)
    .replace(/\//g, "/")
    .replace(",", "");
};
