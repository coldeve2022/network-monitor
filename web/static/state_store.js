(function(root){'use strict';
 class SnapshotGate {
  constructor(){this.ticket=0;this.acceptedTicket=0;this.current=null;this.liveSequence=0;}
  begin(){return ++this.ticket;}
  accept(snapshot,ticket){
   if(ticket<this.acceptedTicket)return false;
   if(this.current&&snapshot.session_id===this.current.session_id){
    if((snapshot.time_epoch||0)<(this.current.time_epoch||0))return false;
    if((snapshot.config_revision||0)<(this.current.config_revision||0))return false;
    if((snapshot.config_revision||0)===(this.current.config_revision||0)&&snapshot.last_sequence!=null&&snapshot.last_sequence<this.liveSequence)return false;
   }
   if(this.current&&snapshot.session_id!==this.current.session_id)this.liveSequence=0;
   this.current=snapshot;this.acceptedTicket=ticket;this.liveSequence=Math.max(this.liveSequence,snapshot.last_sequence||0);return true;
  }
  noteSample(sample){
   if(this.current&&['tcp','dns','traffic'].includes(sample.type)&&sample.probe_revision!=null&&sample.session_id===this.current.session_id&&sample.probe_revision<(this.current.probe_revision||0))return false;
   if(this.current&&sample.type==='ping'&&sample.session_id===this.current.session_id&&(sample.config_revision||0)<(this.current.config_revision||0))return false;
   if(sample.sequence!=null)this.liveSequence=Math.max(this.liveSequence,sample.sequence);
   return true;
  }
 }
 const api={SnapshotGate};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NMState=api;
})(typeof window!=='undefined'?window:globalThis);
