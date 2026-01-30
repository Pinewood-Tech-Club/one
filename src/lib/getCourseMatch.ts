import courseMatchData from '@/../public/meta/course-match.json';

interface CourseMatch {
  icon: string;
  color: string;
}

export function getCourseMatch(courseName: string): CourseMatch {
  const lowerCourseName = courseName.toLowerCase();

  // Calculate scores for each subject
  const scores = courseMatchData.subjects.map(subject => {
    let totalWeight = 0;

    subject.keywords.forEach(({ keyword, weight }) => {
      if (lowerCourseName.includes(keyword.toLowerCase())) {
        totalWeight += weight;
      }
    });

    return {
      subject: subject.subject,
      icon: subject.icon,
      color: subject.color,
      score: totalWeight
    };
  });

  // Find the subject with the highest score
  const bestMatch = scores.reduce((best, current) =>
    current.score > best.score ? current : best
  );

  // If no matches found (score is 0), use catchAll
  if (bestMatch.score === 0) {
    return {
      icon: courseMatchData.catchAll.icon,
      color: courseMatchData.catchAll.color
    };
  }

  return {
    icon: bestMatch.icon,
    color: bestMatch.color
  };
}
