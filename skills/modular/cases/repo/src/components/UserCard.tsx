export function UserCard({ name, email, onClose }) {
  useEffect(() => { const h = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, [onClose]);
  return (
    <div className="fixed inset-0 bg-black/50">
      <div className="rounded-lg bg-white p-4">
        <h2 className="text-lg font-bold">{name}</h2>
        <p className="text-sm text-gray-500">{email}</p>
        <button onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
