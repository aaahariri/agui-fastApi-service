/**
 * Public Component Showcase Page
 *
 * Displays all A2UI components with their type name and file path shown above each one.
 * Accessible at /showcase-components
 */

import { A2UIRenderer } from '@/components/A2UIRenderer';
import type { A2UIComponent } from '@/lib/a2ui-catalog';

interface ShowcaseItem {
  typeName: string;
  filePath: string;
  component: A2UIComponent;
}

const SHOWCASE_ITEMS: ShowcaseItem[] = [
  // ===== SUMMARY =====
  {
    typeName: 'a2ui.TLDR',
    filePath: 'frontend/src/components/A2UI/Summary/TLDR.tsx',
    component: {
      id: 'tldr-1', type: 'a2ui.TLDR',
      props: {
        summary: 'AI-assisted development teams are 40% more productive, with measurable improvements in code quality and developer satisfaction.',
        bullets: ['AI reduces debugging time by 30%', 'Code quality metrics up 25%', 'Developer satisfaction significantly improved']
      }
    }
  },
  {
    typeName: 'a2ui.KeyTakeaways',
    filePath: 'frontend/src/components/A2UI/Summary/KeyTakeaways.tsx',
    component: {
      id: 'takeaways-1', type: 'a2ui.KeyTakeaways',
      props: {
        title: 'Key Takeaways',
        items: ['AI-assisted development is becoming mainstream', 'Best results come from human-AI collaboration', 'Training and onboarding are essential for adoption']
      }
    }
  },
  {
    typeName: 'a2ui.ExecutiveSummary',
    filePath: 'frontend/src/components/A2UI/Summary/ExecutiveSummary.tsx',
    component: {
      id: 'exec-1', type: 'a2ui.ExecutiveSummary',
      props: {
        title: 'Q4 2024 AI Adoption Report',
        summary: 'Enterprise AI adoption accelerated significantly in Q4 2024, with 73% of Fortune 500 companies deploying at least one AI-powered workflow.',
        highlights: ['73% adoption rate among Fortune 500', '$12B market size projected for 2025', 'Productivity gains averaging 38%']
      }
    }
  },
  {
    typeName: 'a2ui.TableOfContents',
    filePath: 'frontend/src/components/A2UI/Summary/TableOfContents.tsx',
    component: {
      id: 'toc-1', type: 'a2ui.TableOfContents',
      props: {
        items: [
          { title: 'Introduction', anchor: '#intro' },
          { title: 'Key Findings', anchor: '#findings' },
          { title: 'Data Analysis', anchor: '#data' },
          { title: 'Conclusion', anchor: '#conclusion' }
        ]
      }
    }
  },

  // ===== DATA =====
  {
    typeName: 'a2ui.StatCard',
    filePath: 'frontend/src/components/A2UI/Data/StatCard.tsx',
    component: {
      id: 'stat-1', type: 'a2ui.StatCard',
      props: { label: 'Total Users', value: '2.4M', trend: '+12%', icon: '👥' }
    }
  },
  {
    typeName: 'a2ui.MetricRow',
    filePath: 'frontend/src/components/A2UI/Data/MetricRow.tsx',
    component: {
      id: 'metric-1', type: 'a2ui.MetricRow',
      props: {
        metrics: [
          { label: 'Revenue', value: '$4.2M', trend: '+8%' },
          { label: 'Signups', value: '12,400', trend: '+22%' },
          { label: 'Churn', value: '2.1%', trend: '-0.4%' }
        ]
      }
    }
  },
  {
    typeName: 'a2ui.ProgressRing',
    filePath: 'frontend/src/components/A2UI/Data/ProgressRing.tsx',
    component: {
      id: 'ring-1', type: 'a2ui.ProgressRing',
      props: { percentage: 78, label: 'Project Completion' }
    }
  },
  {
    typeName: 'a2ui.ComparisonBar',
    filePath: 'frontend/src/components/A2UI/Data/ComparisonBar.tsx',
    component: {
      id: 'cbar-1', type: 'a2ui.ComparisonBar',
      props: { label: 'Market Share', value_a: 65, value_b: 45, label_a: 'Our Product', label_b: 'Competitor' }
    }
  },
  {
    typeName: 'a2ui.DataTable',
    filePath: 'frontend/src/components/A2UI/Data/DataTable.tsx',
    component: {
      id: 'table-1', type: 'a2ui.DataTable',
      props: {
        headers: ['Feature', 'Status', 'Progress', 'Due Date'],
        rows: [
          ['Authentication', 'Complete', '100%', 'Jan 15'],
          ['Dashboard', 'In Progress', '75%', 'Feb 1'],
          ['API Integration', 'Pending', '30%', 'Feb 15'],
        ],
        sortable: true,
        caption: 'Project Feature Status'
      }
    }
  },
  {
    typeName: 'a2ui.MiniChart',
    filePath: 'frontend/src/components/A2UI/Data/MiniChart.tsx',
    component: {
      id: 'chart-1', type: 'a2ui.MiniChart',
      props: { data: [12, 19, 15, 25, 22, 30, 28], label: 'Weekly Activity', type: 'bar' }
    }
  },

  // ===== LISTS =====
  {
    typeName: 'a2ui.RankedItem',
    filePath: 'frontend/src/components/A2UI/Lists/RankedItem.tsx',
    component: {
      id: 'rank-1', type: 'a2ui.RankedItem',
      props: { rank: 1, label: 'AI / Machine Learning', description: 'Leading innovation across industries', score: 98 }
    }
  },
  {
    typeName: 'a2ui.ChecklistItem',
    filePath: 'frontend/src/components/A2UI/Lists/ChecklistItem.tsx',
    component: {
      id: 'check-1', type: 'a2ui.ChecklistItem',
      props: { label: 'Configure CI/CD pipeline', checked: true }
    }
  },
  {
    typeName: 'a2ui.ProConItem',
    filePath: 'frontend/src/components/A2UI/Lists/ProConItem.tsx',
    component: { id: 'procon-pro', type: 'a2ui.ProConItem', props: { type: 'pro', label: 'Fast development cycles', description: 'Enables rapid iteration and quick delivery' } }
  },
  {
    typeName: 'a2ui.ProConItem',
    filePath: 'frontend/src/components/A2UI/Lists/ProConItem.tsx',
    component: { id: 'procon-con', type: 'a2ui.ProConItem', props: { type: 'con', label: 'Steep learning curve', description: 'Takes significant time to fully master' } }
  },
  {
    typeName: 'a2ui.BulletPoint',
    filePath: 'frontend/src/components/A2UI/Lists/BulletPoint.tsx',
    component: { id: 'bullet-1', type: 'a2ui.BulletPoint', props: { text: 'Deploy to staging environment', icon: '→' } }
  },
  {
    typeName: 'a2ui.BulletPoint',
    filePath: 'frontend/src/components/A2UI/Lists/BulletPoint.tsx',
    component: { id: 'bullet-2', type: 'a2ui.BulletPoint', props: { text: 'Run integration tests against staging', level: 1 } }
  },
  {
    typeName: 'a2ui.BulletPoint',
    filePath: 'frontend/src/components/A2UI/Lists/BulletPoint.tsx',
    component: { id: 'bullet-3', type: 'a2ui.BulletPoint', props: { text: 'Notify stakeholders of release', icon: '→' } }
  },

  // ===== RESOURCES =====
  {
    typeName: 'a2ui.LinkCard',
    filePath: 'frontend/src/components/A2UI/Resources/LinkCard.tsx',
    component: {
      id: 'link-1', type: 'a2ui.LinkCard',
      props: { title: 'MDN Web Docs', url: 'https://developer.mozilla.org', description: 'Comprehensive web development documentation' }
    }
  },
  {
    typeName: 'a2ui.ToolCard',
    filePath: 'frontend/src/components/A2UI/Resources/ToolCard.tsx',
    component: {
      id: 'tool-1', type: 'a2ui.ToolCard',
      props: { name: 'VS Code', description: 'Powerful code editor with extensive extensions', features: ['IntelliSense', 'Debugging', 'Git Integration'], url: 'https://code.visualstudio.com' }
    }
  },
  {
    typeName: 'a2ui.BookCard',
    filePath: 'frontend/src/components/A2UI/Resources/BookCard.tsx',
    component: {
      id: 'book-1', type: 'a2ui.BookCard',
      props: { title: 'Clean Code', author: 'Robert C. Martin', description: 'A handbook of agile software craftsmanship', rating: 5 }
    }
  },
  {
    typeName: 'a2ui.RepoCard',
    filePath: 'frontend/src/components/A2UI/Resources/RepoCard.tsx',
    component: {
      id: 'repo-1', type: 'a2ui.RepoCard',
      props: { name: 'awesome-react', description: 'A collection of awesome things regarding React ecosystem', stars: 58000, forks: 7200, language: 'JavaScript' }
    }
  },

  // ===== PEOPLE =====
  {
    typeName: 'a2ui.ProfileCard',
    filePath: 'frontend/src/components/A2UI/People/ProfileCard.tsx',
    component: {
      id: 'profile-1', type: 'a2ui.ProfileCard',
      props: { name: 'Jane Smith', title: 'Senior Software Engineer', bio: 'Passionate about building scalable distributed systems and developer tooling.', tags: ['TypeScript', 'Rust', 'Distributed Systems'] }
    }
  },
  {
    typeName: 'a2ui.CompanyCard',
    filePath: 'frontend/src/components/A2UI/People/CompanyCard.tsx',
    component: {
      id: 'company-1', type: 'a2ui.CompanyCard',
      props: { name: 'Acme Corp', industry: 'SaaS', description: 'Building the future of enterprise software.', founded: '2015', employees: '500-1000' }
    }
  },
  {
    typeName: 'a2ui.QuoteCard',
    filePath: 'frontend/src/components/A2UI/People/QuoteCard.tsx',
    component: {
      id: 'quote-1', type: 'a2ui.QuoteCard',
      props: { quote: 'The best way to predict the future is to create it.', author: 'Peter Drucker', title: 'Management Consultant', context: 'On innovation and leadership' }
    }
  },
  {
    typeName: 'a2ui.ExpertTip',
    filePath: 'frontend/src/components/A2UI/People/ExpertTip.tsx',
    component: {
      id: 'tip-1', type: 'a2ui.ExpertTip',
      props: { tip: 'Always write tests before implementing new features. This TDD approach leads to more maintainable and reliable code.', expert: 'Jane Smith', title: 'Senior Software Engineer', category: 'Best Practices' }
    }
  },

  // ===== NEWS =====
  {
    typeName: 'a2ui.HeadlineCard',
    filePath: 'frontend/src/components/A2UI/News/HeadlineCard.tsx',
    component: {
      id: 'headline-1', type: 'a2ui.HeadlineCard',
      props: { title: 'AI Adoption Reaches New Heights in Enterprise', summary: 'Major corporations report significant ROI from AI implementations across operations.', source: 'Tech Weekly', published_at: '2025-01-30', sentiment: 'positive' }
    }
  },
  {
    typeName: 'a2ui.TrendIndicator',
    filePath: 'frontend/src/components/A2UI/News/TrendIndicator.tsx',
    component: {
      id: 'trend-1', type: 'a2ui.TrendIndicator',
      props: { label: 'AI Tool Adoption', value: '+340%', direction: 'up', context: 'Year over year growth in enterprise AI deployments' }
    }
  },
  {
    typeName: 'a2ui.TimelineEvent',
    filePath: 'frontend/src/components/A2UI/News/TimelineEvent.tsx',
    component: { id: 'timeline-1', type: 'a2ui.TimelineEvent', props: { date: '2022-11-30', title: 'ChatGPT Launched', description: 'OpenAI releases ChatGPT, reaching 1 million users in 5 days.', type: 'milestone' } }
  },
  {
    typeName: 'a2ui.TimelineEvent',
    filePath: 'frontend/src/components/A2UI/News/TimelineEvent.tsx',
    component: { id: 'timeline-2', type: 'a2ui.TimelineEvent', props: { date: '2023-03-14', title: 'GPT-4 Released', description: 'OpenAI launches GPT-4 with significantly improved reasoning and multimodal capabilities.', type: 'milestone' } }
  },
  {
    typeName: 'a2ui.TimelineEvent',
    filePath: 'frontend/src/components/A2UI/News/TimelineEvent.tsx',
    component: { id: 'timeline-3', type: 'a2ui.TimelineEvent', props: { date: '2023-07-18', title: 'Claude 2 Released', description: 'Anthropic releases Claude 2 with a 100K token context window.', type: 'update' } }
  },
  {
    typeName: 'a2ui.TimelineEvent',
    filePath: 'frontend/src/components/A2UI/News/TimelineEvent.tsx',
    component: { id: 'timeline-4', type: 'a2ui.TimelineEvent', props: { date: '2024-05-13', title: 'GPT-4o Announced', description: 'OpenAI unveils GPT-4o with native multimodal and real-time voice capabilities.', type: 'milestone' } }
  },
  {
    typeName: 'a2ui.NewsTicker',
    filePath: 'frontend/src/components/A2UI/News/NewsTicker.tsx',
    component: {
      id: 'ticker-1', type: 'a2ui.NewsTicker',
      props: { items: ['AI funding hits record $50B in Q1', 'New open-source LLM outperforms GPT-4', 'EU AI Act takes effect this month'] }
    }
  },

  // ===== MEDIA =====
  {
    typeName: 'a2ui.VideoCard',
    filePath: 'frontend/src/components/A2UI/Media/VideoCard.tsx',
    component: {
      id: 'video-1', type: 'a2ui.VideoCard',
      props: { title: 'Introduction to Generative AI', channel: 'AI Academy', duration: '42:18', thumbnail: '', url: '#' }
    }
  },
  {
    typeName: 'a2ui.ImageCard',
    filePath: 'frontend/src/components/A2UI/Media/ImageCard.tsx',
    component: {
      id: 'image-1', type: 'a2ui.ImageCard',
      props: { title: 'System Architecture Diagram', caption: 'High-level overview of the microservices topology', url: '#' }
    }
  },
  {
    typeName: 'a2ui.PlaylistCard',
    filePath: 'frontend/src/components/A2UI/Media/PlaylistCard.tsx',
    component: {
      id: 'playlist-1', type: 'a2ui.PlaylistCard',
      props: { title: 'React Masterclass', count: 24, creator: 'Frontend Masters', url: '#' }
    }
  },
  {
    typeName: 'a2ui.PodcastCard',
    filePath: 'frontend/src/components/A2UI/Media/PodcastCard.tsx',
    component: {
      id: 'podcast-1', type: 'a2ui.PodcastCard',
      props: { title: 'The Future of Software Development', show: 'Syntax FM', episode: 'Episode 712', duration: '1:05:30', url: '#' }
    }
  },

  // ===== INSTRUCTIONAL =====
  {
    typeName: 'a2ui.StepCard',
    filePath: 'frontend/src/components/A2UI/Instructional/StepCard.tsx',
    component: {
      id: 'step-1', type: 'a2ui.StepCard',
      props: { step: 1, title: 'Install Dependencies', description: 'Run npm install to set up the project', status: 'completed' }
    }
  },
  {
    typeName: 'a2ui.CodeBlock',
    filePath: 'frontend/src/components/A2UI/Instructional/CodeBlock.tsx',
    component: {
      id: 'code-1', type: 'a2ui.CodeBlock',
      props: {
        code: `const greet = (name: string) => \`Hello, \${name}!\`;\nconsole.log(greet("World"));`,
        language: 'typescript',
        title: 'Example Function'
      }
    }
  },
  {
    typeName: 'a2ui.CalloutCard',
    filePath: 'frontend/src/components/A2UI/Instructional/CalloutCard.tsx',
    component: {
      id: 'callout-1', type: 'a2ui.CalloutCard',
      props: { type: 'info', title: 'Pro Tip', content: 'Use environment variables for all sensitive configuration values to keep your codebase secure.' }
    }
  },
  {
    typeName: 'a2ui.CommandCard',
    filePath: 'frontend/src/components/A2UI/Instructional/CommandCard.tsx',
    component: {
      id: 'command-1', type: 'a2ui.CommandCard',
      props: { command: 'npm run dev', description: 'Start the development server' }
    }
  },

  // ===== COMPARISON =====
  {
    typeName: 'a2ui.VsCard',
    filePath: 'frontend/src/components/A2UI/Comparison/VsCard.tsx',
    component: {
      id: 'vs-1', type: 'a2ui.VsCard',
      props: {
        title_a: 'React', title_b: 'Vue',
        metrics: [
          { label: 'Learning Curve', value_a: 'Moderate', value_b: 'Easy' },
          { label: 'Performance', value_a: 'Excellent', value_b: 'Excellent' },
          { label: 'Community', value_a: 'Massive', value_b: 'Large' },
        ]
      }
    }
  },
  {
    typeName: 'a2ui.ComparisonTable',
    filePath: 'frontend/src/components/A2UI/Comparison/ComparisonTable.tsx',
    component: {
      id: 'ctable-1', type: 'a2ui.ComparisonTable',
      props: {
        title: 'Database Comparison',
        columns: ['Feature', 'PostgreSQL', 'MySQL', 'SQLite'],
        rows: [
          ['ACID compliance', '✓', '✓', '✓'],
          ['JSON support', 'Native', 'Limited', 'Limited'],
          ['Full-text search', 'Built-in', 'Built-in', 'FTS5'],
        ]
      }
    }
  },
  {
    typeName: 'a2ui.FeatureMatrix',
    filePath: 'frontend/src/components/A2UI/Comparison/FeatureMatrix.tsx',
    component: {
      id: 'matrix-1', type: 'a2ui.FeatureMatrix',
      props: {
        features: ['Auth', 'Analytics', 'Storage', 'Realtime'],
        items: [
          { name: 'Starter', features: [true, false, true, false] },
          { name: 'Pro', features: [true, true, true, true] },
          { name: 'Enterprise', features: [true, true, true, true] },
        ]
      }
    }
  },
  {
    typeName: 'a2ui.PricingTable',
    filePath: 'frontend/src/components/A2UI/Comparison/PricingTable.tsx',
    component: {
      id: 'pricing-1', type: 'a2ui.PricingTable',
      props: {
        plans: [
          { name: 'Free', price: '$0/mo', features: ['1 project', '1 GB storage', 'Community support'] },
          { name: 'Pro', price: '$29/mo', features: ['Unlimited projects', '50 GB storage', 'Priority support'], highlighted: true },
          { name: 'Enterprise', price: 'Custom', features: ['Unlimited everything', 'SLA', 'Dedicated support'] },
        ]
      }
    }
  },

  // ===== LAYOUT =====
  {
    typeName: 'a2ui.Section',
    filePath: 'frontend/src/components/A2UI/Layout/Section.tsx',
    component: {
      id: 'section-1', type: 'a2ui.Section',
      props: { title: 'Section Title', subtitle: 'Optional subtitle for additional context' },
      children: [
        { id: 'inner-stat', type: 'a2ui.StatCard', props: { label: 'Example Stat', value: '99%', trend: '+5%' } }
      ]
    }
  },
  {
    typeName: 'a2ui.Grid',
    filePath: 'frontend/src/components/A2UI/Layout/Grid.tsx',
    component: {
      id: 'grid-1', type: 'a2ui.Grid',
      props: { columns: 3 },
      children: [
        { id: 'g1', type: 'a2ui.StatCard', props: { label: 'Metric A', value: '1.2K', trend: '+10%' } },
        { id: 'g2', type: 'a2ui.StatCard', props: { label: 'Metric B', value: '840', trend: '-2%' } },
        { id: 'g3', type: 'a2ui.StatCard', props: { label: 'Metric C', value: '99.9%', trend: '+0.1%' } },
      ]
    }
  },
  {
    typeName: 'a2ui.Columns',
    filePath: 'frontend/src/components/A2UI/Layout/Columns.tsx',
    component: {
      id: 'cols-1', type: 'a2ui.Columns',
      props: { columns: 2 },
      children: [
        { id: 'col-a', type: 'a2ui.CalloutCard', props: { type: 'info', title: 'Left Column', content: 'Content placed in the left column.' } },
        { id: 'col-b', type: 'a2ui.CalloutCard', props: { type: 'warning', title: 'Right Column', content: 'Content placed in the right column.' } },
      ]
    }
  },
  {
    typeName: 'a2ui.Tabs',
    filePath: 'frontend/src/components/A2UI/Layout/Tabs.tsx',
    component: {
      id: 'tabs-1', type: 'a2ui.Tabs',
      props: {
        tabs: [
          { id: 'tab-overview', label: 'Overview', content: 'This is the overview tab content.' },
          { id: 'tab-details', label: 'Details', content: 'This is the details tab content.' },
        ]
      }
    }
  },
  {
    typeName: 'a2ui.Accordion',
    filePath: 'frontend/src/components/A2UI/Layout/Accordion.tsx',
    component: {
      id: 'accordion-1', type: 'a2ui.Accordion',
      props: {
        items: [
          { label: 'What is this?', content: 'This is an accordion component that collapses and expands content.' },
          { label: 'How does it work?', content: 'Click any header to toggle the visibility of its content.' },
          { label: 'When should I use it?', content: 'Use accordions to progressively disclose content and reduce visual clutter on the page.' },
        ]
      }
    }
  },
  {
    typeName: 'a2ui.Carousel',
    filePath: 'frontend/src/components/A2UI/Layout/Carousel.tsx — children: A2UIComponent[] (backend-compatible)',
    component: {
      id: 'carousel-1', type: 'a2ui.Carousel',
      props: {},
      children: [
        { id: 'slide-1', type: 'a2ui.HeadlineCard', props: { title: 'AI Breakthroughs in 2025', summary: 'Researchers achieve new milestones in multimodal AI and reasoning.', source: 'Tech Weekly', published_at: '2025-01-15', sentiment: 'positive' } },
        { id: 'slide-2', type: 'a2ui.StatCard', props: { label: 'Cloud Adoption Growth', value: '+47%', trend: '+47%', icon: '☁️' } },
        { id: 'slide-3', type: 'a2ui.CalloutCard', props: { type: 'info', title: 'Security First', content: 'Zero-trust architecture becomes the new enterprise standard in 2025.' } },
      ]
    }
  },
  // ===== TAGS =====
  {
    typeName: 'a2ui.TagCloud',
    filePath: 'frontend/src/components/A2UI/Tags/TagCloud.tsx',
    component: {
      id: 'tags-1', type: 'a2ui.TagCloud',
      props: {
        tags: [
          { name: 'React', count: 156 },
          { name: 'TypeScript', count: 142 },
          { name: 'Node.js', count: 98 },
          { name: 'Python', count: 87 },
          { name: 'Docker', count: 76 },
        ]
      }
    }
  },
  {
    typeName: 'a2ui.CategoryBadge',
    filePath: 'frontend/src/components/A2UI/Tags/CategoryBadge.tsx',
    component: {
      id: 'catbadge-1', type: 'a2ui.CategoryBadge',
      props: { category: 'Engineering', color: 'blue' }
    }
  },
  {
    typeName: 'a2ui.DifficultyBadge',
    filePath: 'frontend/src/components/A2UI/Tags/DifficultyBadge.tsx',
    component: {
      id: 'diff-1', type: 'a2ui.DifficultyBadge',
      props: { difficulty: 'intermediate' }
    }
  },
  {
    typeName: 'a2ui.StatusIndicator',
    filePath: 'frontend/src/components/A2UI/Tags/StatusIndicator.tsx',
    component: {
      id: 'status-1', type: 'a2ui.StatusIndicator',
      props: { status: 'active', label: 'System Online', pulse: true }
    }
  },
  {
    typeName: 'a2ui.PriorityBadge',
    filePath: 'frontend/src/components/A2UI/Tags/PriorityBadge.tsx',
    component: {
      id: 'priority-1', type: 'a2ui.PriorityBadge',
      props: { priority: 'high' }
    }
  },
  {
    typeName: 'a2ui.Tag',
    filePath: 'frontend/src/lib/a2ui-catalog.tsx (inline)',
    component: {
      id: 'tag-1', type: 'a2ui.Tag',
      props: { label: 'machine-learning', color: 'blue' }
    }
  },
  {
    typeName: 'a2ui.Badge',
    filePath: 'frontend/src/lib/a2ui-catalog.tsx (inline)',
    component: {
      id: 'badge-1', type: 'a2ui.Badge',
      props: { label: 'New', variant: 'default', icon: '✨' }
    }
  },
  {
    typeName: 'a2ui.CategoryTag',
    filePath: 'frontend/src/lib/a2ui-catalog.tsx (inline)',
    component: {
      id: 'cattag-1', type: 'a2ui.CategoryTag',
      props: { category: 'AI / ML', count: 42 }
    }
  },
];

function ComponentCard({ typeName, filePath, component }: ShowcaseItem) {
  return (
    <div className="rounded-xl border border-blue-500/20 overflow-hidden bg-card/60">
      {/* Header */}
      <div className="px-4 py-3 bg-secondary/40 border-b border-blue-500/20">
        <p className="font-mono text-sm font-semibold text-blue-300">{typeName}</p>
        <p className="font-mono text-xs text-muted-foreground mt-0.5">{filePath}</p>
      </div>
      {/* Component preview */}
      <div className="p-4">
        <A2UIRenderer component={component} />
      </div>
    </div>
  );
}

export function ComponentShowcasePage() {
  const categories = [
    { label: 'Summary', prefix: 'a2ui.TLDR a2ui.KeyTakeaways a2ui.ExecutiveSummary a2ui.TableOfContents'.split(' ') },
    { label: 'Data', prefix: 'a2ui.StatCard a2ui.MetricRow a2ui.ProgressRing a2ui.ComparisonBar a2ui.DataTable a2ui.MiniChart'.split(' ') },
    { label: 'Lists', prefix: 'a2ui.RankedItem a2ui.ChecklistItem a2ui.ProConItem a2ui.BulletPoint'.split(' ') },
    { label: 'Resources', prefix: 'a2ui.LinkCard a2ui.ToolCard a2ui.BookCard a2ui.RepoCard'.split(' ') },
    { label: 'People', prefix: 'a2ui.ProfileCard a2ui.CompanyCard a2ui.QuoteCard a2ui.ExpertTip'.split(' ') },
    { label: 'News', prefix: 'a2ui.HeadlineCard a2ui.TrendIndicator a2ui.TimelineEvent a2ui.NewsTicker'.split(' ') },
    { label: 'Media', prefix: 'a2ui.VideoCard a2ui.ImageCard a2ui.PlaylistCard a2ui.PodcastCard'.split(' ') },
    { label: 'Instructional', prefix: 'a2ui.StepCard a2ui.CodeBlock a2ui.CalloutCard a2ui.CommandCard'.split(' ') },
    { label: 'Comparison', prefix: 'a2ui.VsCard a2ui.ComparisonTable a2ui.FeatureMatrix a2ui.PricingTable'.split(' ') },
    { label: 'Layout', prefix: 'a2ui.Section a2ui.Grid a2ui.Columns a2ui.Tabs a2ui.Accordion a2ui.Carousel'.split(' ') },
    { label: 'Tags', prefix: 'a2ui.TagCloud a2ui.CategoryBadge a2ui.DifficultyBadge a2ui.StatusIndicator a2ui.PriorityBadge a2ui.Tag a2ui.Badge a2ui.CategoryTag'.split(' ') },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Page header */}
      <div className="sticky top-0 z-10 border-b border-blue-500/20 bg-card/95 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-white to-blue-200 bg-clip-text text-transparent">
            A2UI Component Showcase
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {SHOWCASE_ITEMS.length} components — each shown with its <span className="font-mono text-blue-400">a2ui.*</span> type name and source file path
          </p>
        </div>
        <div className="h-px bg-gradient-to-r from-transparent via-blue-500/50 to-transparent" />
      </div>

      <div className="max-w-7xl mx-auto px-6 py-10 space-y-16">
        {categories.map(({ label, prefix }) => {
          const items = SHOWCASE_ITEMS.filter(item => prefix.includes(item.typeName));
          if (items.length === 0) return null;
          return (
            <section key={label}>
              <h2 className="text-lg font-semibold text-blue-200 mb-5 pb-2 border-b border-blue-500/20">
                {label}
                <span className="ml-2 text-xs font-normal text-muted-foreground">{items.length} component{items.length !== 1 ? 's' : ''}</span>
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {items.map(item => (
                  <ComponentCard key={item.typeName} {...item} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

export default ComponentShowcasePage;
