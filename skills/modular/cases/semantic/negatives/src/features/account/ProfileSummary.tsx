import { Mail } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

export function ProfileSummary({ user, onEdit }: { user: User; onEdit: () => void }) {
  const initials = user.name.split(" ").map((p) => p[0]).join("").slice(0, 2);
  const joined = new Date(user.createdAt).toLocaleDateString();
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-4">
        <Avatar className="h-12 w-12">
          <AvatarImage src={user.avatarUrl} alt={user.name} />
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div>
          <CardTitle>{user.name}</CardTitle>
          <CardDescription>Member since {joined}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="text-sm">
        <a href={`mailto:${user.email}`} className="flex items-center gap-2 text-muted-foreground hover:underline">
          <Mail className="h-4 w-4" />
          {user.email}
        </a>
      </CardContent>
      <CardFooter className="justify-end">
        <Button variant="outline" onClick={onEdit}>Edit profile</Button>
      </CardFooter>
    </Card>
  );
}
