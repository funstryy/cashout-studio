import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import AceStepPage from './views/ace-step/AceStepPage.vue'
import LoraTrainingPage from './views/ace-step/LoraTrainingPage.vue'
import StableAudioPage from './views/stable-audio/StableAudioPage.vue'
import TrebloPage from './views/treblo/TrebloPage.vue'
import VoicesPage from './views/voices/VoicesPage.vue'
import SeparationPage from './views/separation/SeparationPage.vue'
import ProjectsListPage from './views/editor/ProjectsListPage.vue'
import EditorPage from './views/editor/EditorPage.vue'
import HelpPage from './views/HelpPage.vue'
import AboutPage from './views/AboutPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/ace-step', name: 'ace-step', component: AceStepPage },
    { path: '/ace-step/lora', name: 'ace-step-lora', component: LoraTrainingPage },
    { path: '/stable-audio', name: 'stable-audio', component: StableAudioPage },
    { path: '/treblo', name: 'treblo', component: TrebloPage },
    // /yue2 kept as a redirect rather than deleted: the engine still runs
    // (voices and separation live in the same server) and old bookmarks
    // should land somewhere useful instead of a blank page.
    { path: '/yue2', redirect: '/stable-audio' },
    { path: '/voices', name: 'voices', component: VoicesPage },
    { path: '/separation', name: 'separation', component: SeparationPage },
    { path: '/editor', name: 'editor-projects', component: ProjectsListPage },
    { path: '/editor/:id', name: 'editor', component: EditorPage, props: true },
    { path: '/help', name: 'help', component: HelpPage },
    { path: '/about', name: 'about', component: AboutPage },
  ],
})

export default router
