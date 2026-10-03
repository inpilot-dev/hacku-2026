import { MapPin } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { PageHeader } from '../components/chat';
import DeliveryDetails from '../components/DeliveryDetails';

/* Your details: what the Buy agent types into a shop's guest checkout. */

export default function ProfileTab({ onSaved, returning }: { onSaved?: () => void; returning?: boolean }) {
  return <div className="space-y-6">
    <PageHeader title="Profile" description="Your delivery details." />
    <Card id="delivery">
      <CardHeader><CardTitle className="flex items-center gap-2"><MapPin className="size-4" />Delivery details</CardTitle>
        <CardDescription>Shops get these at guest checkout. They’re also sent to the browser agent’s models to fill the form.</CardDescription></CardHeader>
      <CardContent><DeliveryDetails onSaved={onSaved} returning={returning} /></CardContent>
    </Card>
  </div>;
}
