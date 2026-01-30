'use client';

import { useEffect } from 'react';
import { useQuery } from 'convex/react';
import { api } from '../../../convex/_generated/api';
import { useLoading } from '@/context/LoadingContext';
import { Carosuel, CarosuelItem } from "./Carosuel";
import { AssignmentCard } from "./AssignmentCard";

// Transform Schoology date string to Unix timestamp (seconds)
function parseSchoologyDate(dateStr: string): number {
  if (!dateStr) return 0;
  // Format: "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD"
  const date = new Date(dateStr.replace(' ', 'T'));
  return Math.floor(date.getTime() / 1000);
}

// Construct Schoology assignment link
function buildSchoologyLink(assignmentId: string | number): string {
  return `https://schoology.pinewood.edu/assignment/${assignmentId}/info`;
}

// Transform Convex data to AssignmentCard props
interface AssignmentCardData {
  id: number;
  name: string;
  due: number;
  course: string;
  section: string;
  description: string;
  schoologyLink: string;
}

function transformAssignment(item: any): AssignmentCardData {
  return {
    id: Number(item.id || 0),
    name: item.title || 'Untitled Assignment',
    due: parseSchoologyDate(item.due || ''),
    course: item.course_title || 'Unknown Course',
    section: item.section_title || '',
    description: item.description || '',
    schoologyLink: buildSchoologyLink(item.id),
  };
}

export function UpcomingAssignmentsCarosuel() {
  const { setLoading } = useLoading();
  const upcomingAssignments = useQuery(api.schoologyCache.getUpcoming);

  // Manage loading state based on query status
  useEffect(() => {
    // undefined = loading, null/array = loaded
    const isLoading = upcomingAssignments === undefined;
    setLoading('upcoming-data', isLoading);

    return () => {
      // Cleanup: remove loading state when component unmounts
      setLoading('upcoming-data', false);
    };
  }, [upcomingAssignments, setLoading]);

  // Still loading
  if (upcomingAssignments === undefined) {
    return null; // Loading screen handles this
  }

  // Empty state
  if (!upcomingAssignments || upcomingAssignments.length === 0) {
    return (
      <div className="bg-gray-100 dark:bg-gray-800 p-8 rounded-lg text-center">
        <p className="text-white text-lg font-semibold">No upcoming assignments!</p>
        <p className="text-green-200 text-sm mt-2">Enjoy your free time.</p>
      </div>
    );
  }

  // Transform and render assignments
  const assignments = upcomingAssignments.map(transformAssignment);

  return (
    <div className="bg-gray-100 dark:bg-gray-800">
      <Carosuel className="flex gap-4 overflow-x-auto p-4 items-stretch">
        {assignments.map((assignment) => (
          <CarosuelItem key={assignment.id} className="flex">
            <AssignmentCard
              id={assignment.id}
              name={assignment.name}
              due={assignment.due}
              course={assignment.course}
              section={assignment.section}
              description={assignment.description}
              schoologyLink={assignment.schoologyLink}
            />
          </CarosuelItem>
        ))}
      </Carosuel>
    </div>
  );
}