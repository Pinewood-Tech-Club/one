'use client';

import { SchoologyIcon } from "@/components/icons/SchoologyIcon";
import { formatRelativeDate } from "@/lib/formatRelativeDate";

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

  return (
    <div className="bg-white p-4 rounded-lg shadow-md w-96 flex flex-col flex-1">
      <h3 className="font-bold text-green-800 text-xl">{name}</h3>
      <p>Due {time ? (
        <><span className="font-medium">{day}</span> at <span className="font-medium">{time}</span></>
      ) : (
        <span className="font-medium">{day}</span>
      )}</p>
      <p>{course}: {section}</p>
      <p className="mt-2 text-sm text-gray-500">{description}</p>

      <div className="flex justify-between mt-auto pt-4">
        <a href={schoologyLink} target="_blank" rel="noopener noreferrer" className="text-red-600 font-semibold cursor-pointer">
          <SchoologyIcon />
        </a>
        <button className="text-green-800 font-semibold cursor-pointer">View Details</button>
      </div>
    </div>
  );
}
