import { FolderPlus } from "lucide-react";
import { Button } from "@/components/ui/button";

export function NoProjects({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed p-10 text-center">
      <FolderPlus className="h-10 w-10 text-muted-foreground" />
      <h3 className="mt-4 text-lg font-semibold">No projects yet</h3>
      <p className="mt-1 text-sm text-muted-foreground">Create your first project to get started.</p>
      <Button className="mt-6" onClick={onCreate}>New project</Button>
    </div>
  );
}
