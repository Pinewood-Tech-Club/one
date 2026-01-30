'use client';

import { Carosuel, CarosuelItem } from "./Carosuel";
import { AssignmentCard } from "./AssignmentCard";

export function UpcomingAssignmentsCarosuel() {
    return (
        <div className="bg-green-800">
            <Carosuel className="flex gap-4 overflow-x-auto p-4 items-stretch">
                <CarosuelItem className="flex">
                    <AssignmentCard
                        id={123}
                        name="HW 11.3"
                        due={Math.floor(Date.now() / 1000) + 86400 * 3}
                        course="Chemistry Honors"
                        section="C Period"
                        description="Watch the 11.3 EdPuzzle where we left off in class In the textbook do #27, 28, 32"
                        schoologyLink="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                    />
                </CarosuelItem>

                <CarosuelItem className="flex">
                    <AssignmentCard
                        id={123}
                        name="SAP 2.0—Expository Section of Outline"
                        due={Math.floor(Date.now() / 1000) + 86400}
                        course="Writing 10"
                        section="D Period"
                        description="Complete this in the outline document I created for you"
                        schoologyLink="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                    />
                </CarosuelItem>
            </Carosuel>
        </div>
    );
}