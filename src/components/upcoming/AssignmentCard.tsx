'use client';

import { IconWrapper } from "@/components/icons/IconWrapper";
import { formatRelativeDate } from "@/lib/formatRelativeDate";
import { getCourseMatch } from "@/lib/getCourseMatch";
import posthog from 'posthog-js';

interface AssignmentCardProps {
  id: number;
  name: string;
  due: number;
  course: string;
  section: string;
  description: string;
  schoologyLink: string;
}

export function AssignmentCard({ id, name, due, course, section, description, schoologyLink }: AssignmentCardProps) {
  const { day, time } = formatRelativeDate(due);
  const { icon, color } = getCourseMatch(course);

  return (
    <div className="bg-white p-3 rounded-lg w-96 flex flex-col flex-1" style={{ border: `2px solid ${color}` }}>
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg" style={{backgroundColor: color}}>
          <IconWrapper src={icon} alt={course} color="#fff" />
        </div>
        <div className="flex-1 min-w-0">
          <h3
            className="font-bold text-lg"
            data-ph-mask
            style={{
              color: color,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              lineHeight: '1.2'
            }}
          >
            {name}
          </h3>
          <p className="text-sm" data-ph-mask>Due {time ? (
            <><span className="font-medium">{day}</span> at <span className="font-medium">{time}</span></>
          ) : (
            <span className="font-medium">{day}</span>
          )}</p>
          <p className="overflow-hidden text-ellipsis whitespace-nowrap text-xs" data-ph-mask>{course}: {section}</p>
        </div>
      </div>
      {description && (
        <p
          className="mt-2 text-xs text-gray-500"
          data-ph-mask
          style={{
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            textOverflow: 'ellipsis'
          }}
        >
          {description}
        </p>
      )}

      <div className="flex justify-between mt-auto pt-4">
        <a
          href={schoologyLink}
          target="_blank"
          rel="noopener noreferrer"
          className="text-red-600 font-semibold cursor-pointer"
          onClick={() => posthog.capture('schoology_link_clicked', { assignmentId: id })}
        >
          <IconWrapper src="/icons/schoology.svg" className="w-6 h-6" color={color} />
        </a>
        <button className="text-white py-0.5 px-1 rounded-sm cursor-pointer" style={{backgroundColor: color}}>View Details</button>
      </div>
    </div>
  );
}
