'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1245-1338). Public "Contact" page. Preserved VERBATIM
// — only the import block at the top is new.

import { useState } from 'react'
import { MapPin, Building } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

export function ContactPage() {
  const [formData, setFormData] = useState({ name: '', email: '', company: '', message: '', inquiryType: '' })

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-foreground">Get in Touch</h1>
        <p className="text-lg text-muted-foreground mt-3">We'd love to hear from you. Let us know how we can help.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
        {/* Contact Form */}
        <Card>
          <CardHeader>
            <CardTitle>Send us a message</CardTitle>
            <CardDescription>Fill out the form and we'll get back to you within 24 hours.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="contact-name">Name</Label>
                <Input id="contact-name" placeholder="Your name" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="contact-email">Email</Label>
                <Input id="contact-email" type="email" placeholder="you@company.com" value={formData.email} onChange={e => setFormData({ ...formData, email: e.target.value })} />
              </div>
            </div>
            <div>
              <Label htmlFor="contact-company">Company</Label>
              <Input id="contact-company" placeholder="Your organization" value={formData.company} onChange={e => setFormData({ ...formData, company: e.target.value })} />
            </div>
            <div>
              <Label>Inquiry Type</Label>
              <Select value={formData.inquiryType} onValueChange={v => setFormData({ ...formData, inquiryType: v })}>
                <SelectTrigger><SelectValue placeholder="Select inquiry type" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="sales">Sales Inquiry</SelectItem>
                  <SelectItem value="support">Technical Support</SelectItem>
                  <SelectItem value="partnership">Partnership</SelectItem>
                  <SelectItem value="academic">Academic Access</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label htmlFor="contact-message">Message</Label>
              <Textarea id="contact-message" placeholder="Tell us about your needs..." rows={4} value={formData.message} onChange={e => setFormData({ ...formData, message: e.target.value })} />
            </div>
            <Button className="w-full bg-[#5B4FCF] hover:bg-[#4B3FBF]">Send Message</Button>
          </CardContent>
        </Card>

        {/* Office Info */}
        <div className="space-y-6">
          <Card>
            <CardContent className="pt-6">
              <div className="h-48 bg-slate-100 rounded-lg flex items-center justify-center mb-4">
                <MapPin className="w-12 h-12 text-muted-foreground/30" />
              </div>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {[
              { city: 'San Francisco', address: '535 Mission St, Suite 1400', icon: <Building className="w-5 h-5" /> },
              { city: 'Boston', address: '50 Milk St, Floor 16', icon: <Building className="w-5 h-5" /> },
            ].map(office => (
              <Card key={office.city}>
                <CardContent className="pt-6">
                  <div className="flex items-center gap-2 mb-2 text-[#5B4FCF]">{office.icon}<span className="font-semibold">{office.city}</span></div>
                  <p className="text-sm text-muted-foreground">{office.address}</p>
                </CardContent>
              </Card>
            ))}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Partner with DrugOS</CardTitle>
              <CardDescription>Interested in integrating DrugOS into your workflow?</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground mb-4">
                We work with pharmaceutical companies, CROs, academic institutions, and rare disease foundations.
              </p>
              <Button variant="outline" className="w-full">Learn About Partnerships</Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
