'use client'

// FE-023 ROOT FIX (Teammate 17 Subagent C): extracted from app-router.tsx
// (originally lines 1192-1239). Public "Blog & News" page. Preserved
// VERBATIM — only the import block at the top is new.
//
// The module-level `blogPosts` constant was defined at the top of
// app-router.tsx (lines 123-127) but used only by BlogPage. Moved here as
// a local declaration per hostile-auditor rule 4. The FE-065 comment that
// explained the placeholder has been preserved verbatim.

import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

// FE-065: Empty placeholder — integrate a CMS for blog content.
const blogPosts: Array<{
  id: string; title: string; excerpt: string; category: string
  date: string; author: string; readTime: string
}> = []

export function BlogPage() {
  const [activeCategory, setActiveCategory] = useState('All')
  const categories = ['All', 'Research', 'Technology', 'Partnerships']
  const filtered = activeCategory === 'All' ? blogPosts : blogPosts.filter(p => p.category === activeCategory)

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-20">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold text-foreground">Blog & News</h1>
        <p className="text-lg text-muted-foreground mt-3">Latest updates from DrugOS research and engineering</p>
      </div>

      {/* Category Tabs */}
      <div className="flex items-center gap-2 mb-8 flex-wrap">
        {categories.map(cat => (
          <Button
            key={cat}
            variant={activeCategory === cat ? 'default' : 'outline'}
            size="sm"
            onClick={() => setActiveCategory(cat)}
            className={activeCategory === cat ? 'bg-[#5B4FCF] hover:bg-[#4B3FBF]' : ''}
          >
            {cat}
          </Button>
        ))}
      </div>

      {/* Blog Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filtered.map(post => (
          <Card key={post.id} className="hover:shadow-lg transition-shadow cursor-pointer">
            <CardContent className="pt-6">
              <div className="flex items-center gap-2 mb-3">
                <Badge variant="secondary">{post.category}</Badge>
                <span className="text-xs text-muted-foreground">{post.date}</span>
              </div>
              <h3 className="text-lg font-semibold text-foreground leading-snug mb-2">{post.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{post.excerpt}</p>
              <p className="text-sm text-[#5B4FCF] mt-4 flex items-center gap-1">
                Read more <ArrowRight className="w-3 h-3" />
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
