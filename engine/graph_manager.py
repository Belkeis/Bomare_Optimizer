# engine/graph_manager
import networkx as nx # a library to create the graphical representaion 
import pandas as pd 
import ast

class RawTableGraph :
    def __init__( self , df: pd.DataFrame):
        self.df=df
        self.graph = self.build_dag(df)

    def build_dag(self ,df): # this function will return an nx digraph object 
        G = nx.DiGraph()  #Create an empty graph to start filling in

        for idx ,row in df.iterrows(): #return (index , row data)
            task_id =row['Id'] if 'Id' in row and pd.notna(row['Id']) else idx #check if the row contain a column id and it value is not null

            # add the node to the graph 
            G.add_node(task_id , cycle_time=row.get('CycleTime', 0))


            # handle the relation typing error 
            relations = row.get ('Relation',None)  # we use get such if the row contain no relations values will not have error 
            if isinstance(relations , str): #if relations is string we cnvrt it into string or tuple 
                try:
                    relations = ast.literal_eval(relations) 
                except(ValueError, SyntaxError) :
                    relations = []


            #if relations are tuple or list we directly
            if isinstance(relations , (list , tuple)):  #[3] , [3,4]
                for parent_id in relations:
                    G.add_edge(parent_id , task_id)

            elif pd.notna(relations) and relations !="":   # when the relation is a only one value like 3  
                  G.add_edge(relations, task_id)


        #Constraint 1: check  that there is no loop 
        if not nx.is_directed_acyclic_graph(G):
            raise ValueError ("Invalid Factory Data: Circular dependencies detected")   

        # Constraint 2: Unidirectional flow (Out-degree <= 1, no branching)
        for node in G.nodes():
            if G.out_degree(node)>1 :
                raise ValueError(f"Task '{node}' branches into multiple paths — not allowed!")   

        return G    
    
    def get_topological_order(self) :#Returns valid processing order where every task comes after its dependencies
        return list(nx.topological_sort(self.graph))  

    def is_valid_station_group(self, task_ids: list):
        subgraph=self.graph.subgraph(task_ids)
        return nx.is_weakly_connected(subgraph)

    





