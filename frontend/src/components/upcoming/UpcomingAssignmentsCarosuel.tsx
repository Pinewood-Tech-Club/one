'use client';

import { useEffect } from 'react';
import { useLoading } from '@/context/LoadingContext';
import type { UpcomingAssignment } from '@/lib/api';
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

// Transform a merged assignment record into AssignmentCard props
interface AssignmentCardData {
  id: number;
  name: string;
  due: number;
  course: string;
  section: string;
  description: string;
  schoologyLink: string;
}

function transformAssignment(item: UpcomingAssignment): AssignmentCardData {
  return {
    id: Number(item.id || 0),
    name: item.title || 'Untitled Assignment',
    due: parseSchoologyDate(String(item.due || '')),
    course: item.course_title || 'Unknown Course',
    section: item.section_title || '',
    description: item.description || '',
    schoologyLink: buildSchoologyLink(item.id ?? 0),
  };
}

interface UpcomingAssignmentsCarosuelProps {
  assignments: UpcomingAssignment[] | undefined;
}

export function UpcomingAssignmentsCarosuel({ assignments: upcomingAssignments }: UpcomingAssignmentsCarosuelProps) {
  const { setLoading } = useLoading();

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
    return;
  }

  // Transform and render assignments
  const assignments = upcomingAssignments.map(transformAssignment);

  return (
    <div>
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