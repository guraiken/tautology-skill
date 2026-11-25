import { UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";

export function MembersEmptyState({ onInvite, canInvite }: { onInvite: () => void; canInvite: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed p-10 text-center">
      <UserPlus className="h-10 w-10 text-muted-foreground" />
      <h3 className="mt-4 text-lg font-semibold">Invite your team</h3>
      <p className="mt-1 text-sm text-muted-foreground">Members can view and edit projects in this workspace.</p>
      {canInvite && (
        <Button variant="outline" className="mt-6" onClick={onInvite}>
          Invite members
        </Button>
      )}
    </div>
  );
}
