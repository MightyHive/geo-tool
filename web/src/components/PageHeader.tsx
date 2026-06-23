export function PageHeader({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <header className="mb-8">
      <h1 className="text-3xl font-bold text-brand-dark tracking-tight">{title}</h1>
      {description && (
        <p className="mt-2 text-gray-600 max-w-2xl">{description}</p>
      )}
    </header>
  );
}
