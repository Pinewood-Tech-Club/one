export interface RelativeDate {
  day: string;
  time: string | null;
}

export function formatRelativeDate(unixTimestamp: number): RelativeDate {
  const date = new Date(unixTimestamp * 1000);
  const now = new Date();

  // Calculate days difference (comparing at midnight)
  const todayMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dateMidnight = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const daysDiff = Math.floor((dateMidnight.getTime() - todayMidnight.getTime()) / (1000 * 60 * 60 * 24));

  // Format time portion
  const timeStr = date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true
  });

  if (daysDiff === 0) return { day: 'Today', time: timeStr };
  if (daysDiff === 1) return { day: 'Tomorrow', time: timeStr };
  if (daysDiff >= 2 && daysDiff <= 6) {
    const dayName = date.toLocaleDateString('en-US', { weekday: 'long' });
    return { day: dayName, time: timeStr };
  }

  // More than a week away - no time, just day name + date
  const dayAndDate = date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric'
  });
  return { day: dayAndDate, time: null };
}
